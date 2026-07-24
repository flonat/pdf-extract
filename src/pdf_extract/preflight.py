"""PDF read-integrity preflight — PASS/FAIL/UNAVAILABLE verdict per document.

Guards the local-extraction channel: PDF readers silently truncate documents
with malformed cross-reference tables and misreport page counts, so a real,
correctly-cited source can acquire an apparently valid page locator derived
from a truncated or mispaginated read. Run this BEFORE trusting page numbers
from a locally-read PDF (quotes "on page N", figure/table locators, split-pdf
page ranges).

Ported 2026-07-24 from Edward Cheng-I Wu's academic-research-skills v3.19.0
(`scripts/pdf_read_preflight.py`, CC-BY-NC 4.0; mechanism observed in
kengo006/alexandria). Adaptation: hashlib inline, package-local module; the
detection logic (three signals + trailing-data + xref-coverage variants) is
kept intact.

Three independent page-count signals must agree:

  1. declared_page_count   — the root page tree's /Count, read from the raw object;
  2. enumerated_page_count — our own recursive /Kids walk counting /Type /Page
                             leaves (cycle-guarded, node-budgeted);
  3. reader_page_count     — pypdf's flattened page list, as a third opinion.

Verdict: PASS only when all three agree, the count is positive, no parser
repair warnings, no trailing data after the final %%EOF, and no xref-coverage
anomaly. FAIL when the parse completed but counts disagree. UNAVAILABLE for
anything the preflight cannot vouch for (unreadable, encrypted, malformed
page tree, /Kids cycle, node-budget hit, pypdf absent, repair warnings even
with agreeing counts). Deliberately uses pypdf, NOT pymupdf: MuPDF repairs
malformed documents silently, which is exactly the signal to surface.

The verdict is data, not an error — consumers branch on the JSON, not exit
codes.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

try:
    import pypdf
except ImportError:  # degrade to UNAVAILABLE
    pypdf = None

TOOL_VERSION = "pdf-extract-preflight/1.0.0"
SCHEMA = "pdf_read_preflight/1"

# Hard ceiling on page-tree nodes visited by the enumeration walk. Real
# documents sit far below this; hitting it means a pathological or adversarial
# tree we must not vouch for (and must not spin on).
NODE_BUDGET = 50_000

PASS, FAIL, UNAVAILABLE = "PASS", "FAIL", "UNAVAILABLE"


class _WarningCollector(logging.Handler):
    """Captures pypdf's parser chatter — repair messages ARE the
    silent-xref-repair signal this preflight exists to surface."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


class _TreeProblem(Exception):
    """Structural page-tree problem that forecloses a confident enumeration."""


def _kid_key(kid):
    """Stable identity for a /Kids entry (indirect ref when available)."""
    ref = getattr(kid, "indirect_reference", None) or (
        kid if hasattr(kid, "idnum") else None
    )
    if ref is not None:
        return ("ref", ref.idnum, ref.generation)
    return ("id", id(kid))


def _walk_page_tree(node, visited, budget):
    """Count /Type /Page leaves under `node`, guarding cycles and runaway trees."""
    count = 0
    stack = [node]
    while stack:
        if len(visited) > budget:
            raise _TreeProblem("page-tree node budget exceeded")
        current = stack.pop()
        key = _kid_key(current)
        if key in visited:
            raise _TreeProblem("page-tree cycle detected")
        visited.add(key)
        obj = current.get_object() if hasattr(current, "get_object") else current
        node_type = str(obj.get("/Type", ""))
        if node_type == "/Page":
            count += 1
        elif node_type == "/Pages":
            kids = obj.get("/Kids", [])
            stack.extend(kids)
        else:
            raise _TreeProblem(f"unexpected page-tree node type {node_type or '(none)'}")
    return count


def run_preflight(path) -> dict:
    """Run the read-integrity preflight on one PDF; always returns a sidecar dict."""
    path = Path(path)
    result = {
        "schema": SCHEMA,
        "verdict": UNAVAILABLE,
        "file": str(path),
        "sha256": None,
        "declared_page_count": None,
        "enumerated_page_count": None,
        "reader_page_count": None,
        "warnings": [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tool": TOOL_VERSION,
    }
    warnings = result["warnings"]

    try:
        data = path.read_bytes()
    except OSError as exc:
        warnings.append(f"unreadable: {exc}")
        return result
    result["sha256"] = hashlib.sha256(data).hexdigest()

    # Structural check independent of the parser: a PDF truncated partway
    # through an incremental update keeps an OLDER valid %%EOF, and pypdf
    # silently reads that previous revision — all three counts then agree on
    # the OLD page tree. Non-whitespace bytes after the LAST %%EOF are that
    # signature: record the warning now, veto PASS at the verdict step. A
    # complete incremental update always ends with its own %%EOF, so
    # legitimate multi-revision files are not flagged. PDF whitespace per ISO
    # 32000 §7.2.2 — NOT Python's: NUL is whitespace (common padding after
    # %%EOF, must not veto), vertical tab 0x0B is NOT.
    _PDF_WS = b"\x00\x09\x0a\x0c\x0d\x20"
    trailing_ok = True
    eof_at = data.rfind(b"%%EOF")
    if eof_at != -1 and data[eof_at + 5 :].translate(None, _PDF_WS):
        trailing_ok = False
        warnings.append(
            f"trailing-data: {len(data) - (eof_at + 5)} bytes after the final %%EOF "
            "include non-whitespace content (possible truncated incremental update)"
        )

    if pypdf is None:
        warnings.append("pypdf-not-installed: preflight cannot parse the document")
        return result

    collector = _WarningCollector()
    pypdf_logger = logging.getLogger("pypdf")
    pypdf_logger.addHandler(collector)
    try:
        try:
            reader = pypdf.PdfReader(io.BytesIO(data))
        except Exception as exc:  # malformed beyond pypdf's tolerance
            warnings.append(f"parse-error: {exc}")
            return result

        if getattr(reader, "is_encrypted", False):
            warnings.append("encrypted: preflight cannot verify an encrypted document")
            return result

        try:
            root = reader.trailer["/Root"].get_object()
            pages_node = root["/Pages"]
            pages_obj = pages_node.get_object()
            raw_count = pages_obj["/Count"]
            # Require an actual PDF integer object. `int()` would coerce a
            # float /Count 2.7 to 2 (or a text string "2") and then agree with
            # two real leaves — a malformed page tree must be UNAVAILABLE,
            # not PASS. pypdf NumberObject subclasses int; FloatObject float.
            if isinstance(raw_count, bool) or not isinstance(raw_count, int):
                warnings.append(
                    f"page-tree-unresolvable: /Count is not an integer object "
                    f"({type(raw_count).__name__}: {raw_count!r})"
                )
                return result
            declared = int(raw_count)
        except Exception as exc:
            warnings.append(f"page-tree-unresolvable: {exc}")
            return result
        result["declared_page_count"] = declared

        try:
            enumerated = _walk_page_tree(pages_node, set(), NODE_BUDGET)
        except Exception as exc:  # incl. _TreeProblem — same degradation either way
            warnings.append(f"page-tree-walk: {exc}")
            return result
        result["enumerated_page_count"] = enumerated

        # The walk above verified the /Kids tree is cycle-free, so flattening
        # the same tree cannot spin.
        try:
            reader_count = len(reader.pages)
        except Exception as exc:
            warnings.append(f"reader-page-list: {exc}")
            return result
        result["reader_page_count"] = reader_count

        # Xref-coverage check: a malformed incremental update can append new
        # objects PLUS a syntactically complete startxref that still points at
        # the PREVIOUS revision's xref, followed by its own %%EOF — the
        # trailing-data check then sees nothing after the final %%EOF while
        # pypdf silently reads the old revision. Cross-check: every raw
        # `N M obj` header in the file must be an object number the parsed
        # xref chain knows about. Best-effort: if pypdf's xref internals are
        # absent, skip rather than crash.
        try:
            xref_map = getattr(reader, "xref", None)
            if isinstance(xref_map, dict) and xref_map:
                known_objs = set()
                for gen_table in xref_map.values():
                    if isinstance(gen_table, dict):
                        known_objs.update(gen_table.keys())
                compressed = getattr(reader, "xref_objStm", None)
                if isinstance(compressed, dict):
                    known_objs.update(compressed.keys())
                # Header token separators implement the FULL ISO 32000 lexer
                # model, not Python's \s: PDF permits bare-CR line endings,
                # treats NUL as whitespace, and treats %-comments-to-EOL as
                # token separators — so `2 0%note\nobj` is a valid header.
                # Numeric tokens carry the full ISO 32000 integer form too:
                # optional sign and leading-zero padding (`+2 0 obj`,
                # `00000000002 0 obj`) must not hide from the scan.
                _ws = rb"[\x00\t\n\x0c\r ]"
                _sep = rb"(?:" + _ws + rb"|%[^\r\n]*[\r\n])"
                _num = rb"[+-]?0*\d{1,10}"
                raw_offsets: dict[int, list[int]] = {}
                for m in re.finditer(
                    rb"(?:^|" + _sep + rb")" + _sep + rb"*(" + _num + rb")" + _sep + rb"+" + _num + _sep + rb"+obj\b",
                    data,
                ):
                    raw_offsets.setdefault(int(m.group(1)), []).append(m.start(1))
                orphaned = set(raw_offsets) - {int(n) for n in known_objs}
                if orphaned:
                    warnings.append(
                        "xref-coverage: object number(s) "
                        f"{sorted(orphaned)[:5]} present in the file but absent from "
                        "the active xref chain (possible stale startxref / "
                        "unreachable newer revision)"
                    )
                    trailing_ok = False
                # Redefined-object variant: a malformed update can append a
                # REPLACEMENT body for an existing object number plus a stale
                # startxref — number-membership alone then sees no orphan
                # while pypdf reads the old copy. The newest raw copy of every
                # directly-stored object must be the one the active chain
                # references. Calibration guard: pypdf applies a global delta
                # when a file has junk before %PDF; if NO active offset
                # matches any raw offset the comparison is uncalibrated —
                # skip rather than mass-flag.
                direct_offsets = {}
                for gen_table in xref_map.values():
                    if isinstance(gen_table, dict):
                        for objnum, off in gen_table.items():
                            if isinstance(off, int) and int(objnum) in raw_offsets:
                                direct_offsets[int(objnum)] = off
                if direct_offsets and any(
                    off in raw_offsets[n] for n, off in direct_offsets.items()
                ):
                    superseded = sorted(
                        n
                        for n, off in direct_offsets.items()
                        if max(raw_offsets[n]) > off
                    )
                    if superseded:
                        warnings.append(
                            "xref-coverage: later unreferenced revision(s) of object "
                            f"number(s) {superseded[:5]} exist after the copy the "
                            "active xref chain references (possible stale startxref)"
                        )
                        trailing_ok = False
                # Compressed-object variant: the active copy of N lives inside
                # an object stream (no direct offset in reader.xref) — but a
                # direct raw replacement of N appended AFTER its container,
                # with a stale startxref, is exactly the unreachable-newer-
                # revision case. A raw copy BEFORE the container is the
                # legitimate superseded-into-objstm update and is not flagged.
                if isinstance(compressed, dict):
                    compressed_superseded = []
                    for objnum, ref in compressed.items():
                        n = int(objnum)
                        if n not in raw_offsets or n in direct_offsets:
                            continue
                        container = ref[0] if isinstance(ref, (tuple, list)) and ref else None
                        container_off = None
                        if container is not None:
                            for gen_table in xref_map.values():
                                if (
                                    isinstance(gen_table, dict)
                                    and container in gen_table
                                    and isinstance(gen_table[container], int)
                                ):
                                    container_off = gen_table[container]
                                    break
                        if container_off is not None and max(raw_offsets[n]) > container_off:
                            compressed_superseded.append(n)
                    if compressed_superseded:
                        warnings.append(
                            "xref-coverage: direct replacement(s) of compressed object "
                            f"number(s) {sorted(compressed_superseded)[:5]} appear after "
                            "their object-stream container (possible stale startxref)"
                        )
                        trailing_ok = False
        except Exception as exc:  # best-effort cross-check, never a crash path
            warnings.append(f"xref-coverage-skipped: {exc}")
    finally:
        pypdf_logger.removeHandler(collector)
        # Append captured parser chatter HERE so every early return above
        # (encryption, unresolvable tree, walk problems) still carries it.
        warnings.extend(f"pypdf: {m}" for m in collector.messages)

    if not (declared == enumerated == reader_count):
        result["verdict"] = FAIL
        return result
    if declared <= 0:
        warnings.append("empty-page-tree: agreeing counts but zero pages")
        return result
    if collector.messages or not trailing_ok:
        # Counts agree, but the parse needed repair or the file carries data
        # after its final %%EOF — cannot vouch.
        return result
    result["verdict"] = PASS
    return result
