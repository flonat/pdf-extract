"""Bulk Marker extraction cron — fills the pdf-extract cache for a
Paperpile library, prioritised by relevance to active projects.

Designed to run nightly via launchd in a time-bounded window. Each run:
  1. Loads Paperpile JSON export.
  2. Builds priority queue (papers in active project bibs first, then long tail).
  3. Skips papers already in the marker cache.
  4. Runs `extract(..., backend='marker')` per paper until time budget exhausted.
  5. Logs structured progress to stdout (captured by launchd to log file).

Cache directory: see `pdf_extract.cache._default_cache_root()`. On macOS this
defaults to `/Volumes/SSD/pdf-extract-cache/` if that volume exists, else
`~/.cache/pdf-extract/`. Override with `PDF_EXTRACT_CACHE_DIR=...`.

Configuration is via env vars (with CLI overrides). All paths must be set —
this script intentionally has no hardcoded user paths so it works on any
machine without modification:

  PAPERPILE_JSON          : path to Paperpile JSON export
  PAPERPILE_PDF_ROOT      : path to local PDF mirror
  RESEARCH_PROJECTS_ROOT  : path to research projects tree (for bib priority);
                            optional — set to empty string to disable priority

Usage:
    bulk_extract.py [--max-runtime SECONDS] [--max-papers N] [--no-prioritise]
                    [--paperpile-json PATH] [--pdf-root PATH]
                    [--projects-root PATH] [--per-paper-timeout SEC]
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

from pdf_extract import extract


def _env_path(var: str) -> Path | None:
    val = os.environ.get(var)
    if not val:
        return None
    return Path(val).expanduser()


DEFAULT_PAPERPILE = _env_path("PAPERPILE_JSON")
DEFAULT_PDF_ROOT = _env_path("PAPERPILE_PDF_ROOT")
DEFAULT_PROJECTS_ROOT = _env_path("RESEARCH_PROJECTS_ROOT")
DEFAULT_MAX_RUNTIME = 6 * 60 * 60  # 6 hours
DEFAULT_PER_PAPER_TIMEOUT = 120    # 2 min Marker hard cap per paper


class TimeBudgetExceeded(Exception):
    pass


def resolve_pdf(entry: dict, pdf_root: Path) -> Path | None:
    for att in entry.get("attachments", []) or []:
        if att.get("mimeType") != "application/pdf":
            continue
        filename = att.get("filename", "")
        if filename.startswith("All Papers/"):
            filename = filename[len("All Papers/"):]
        candidate = pdf_root / filename
        if candidate.exists():
            return candidate
    return None


def collect_bib_citekeys(projects_root: Path) -> set[str]:
    """Scan every paper-*/paper/references.bib (and main.bib) under projects_root.

    Returns the set of citekeys that appear in any project bibliography. These
    are the highest-priority papers to extract — they're actively used in
    drafts.
    """
    keys: set[str] = set()
    if not projects_root.exists():
        return keys
    bib_files = list(projects_root.rglob("paper-*/**/references.bib")) + \
                list(projects_root.rglob("paper-*/**/main.bib"))
    import re
    citekey_re = re.compile(r"^\s*@\w+\s*\{\s*([^,\s]+)\s*,", re.MULTILINE)
    for bib in bib_files:
        try:
            text = bib.read_text(encoding="utf-8", errors="ignore")
            for m in citekey_re.finditer(text):
                keys.add(m.group(1))
        except Exception:
            continue
    return keys


def is_already_cached(pdf_path: Path, cache_root: Path | None = None) -> bool:
    """Cheap check: does the marker cache already have a doc.json for this PDF?"""
    from pdf_extract import cache as _cache, EXTRACT_VERSION
    return _cache.load(pdf_path, "marker", EXTRACT_VERSION, root=cache_root) is not None


def prioritise(
    library: list[dict], pdf_root: Path, projects_root: Path,
) -> list[tuple[dict, Path]]:
    """Build the extraction queue.

    Order:
      1. Papers cited in any active project's bibliography (hot subset)
      2. Recently-added Paperpile entries (sort by `created` desc)
      3. Long tail
    Returns list of (entry, pdf_path) for papers with available PDFs.
    """
    print(f"[prio] scanning project bibs under {projects_root}")
    hot_keys = collect_bib_citekeys(projects_root)
    print(f"[prio] {len(hot_keys)} citekeys found in project bibliographies")

    candidates: list[tuple[dict, Path]] = []
    hot: list[tuple[dict, Path]] = []
    other: list[tuple[dict, Path]] = []
    for entry in library:
        path = resolve_pdf(entry, pdf_root)
        if path is None:
            continue
        if entry.get("citekey") in hot_keys:
            hot.append((entry, path))
        else:
            other.append((entry, path))

    # Sort 'other' by created timestamp descending (recent first)
    other.sort(key=lambda t: t[0].get("created", 0), reverse=True)
    candidates = hot + other
    print(f"[prio] queue: {len(hot)} hot + {len(other)} long-tail = {len(candidates)} total")
    return candidates


def _alarm_handler(signum, frame):
    raise TimeBudgetExceeded()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--max-runtime", type=int, default=DEFAULT_MAX_RUNTIME,
                   help=f"Wall-clock budget in seconds (default {DEFAULT_MAX_RUNTIME})")
    p.add_argument("--max-papers", type=int, default=10000,
                   help="Hard cap on papers per run (safety net)")
    p.add_argument("--per-paper-timeout", type=int, default=DEFAULT_PER_PAPER_TIMEOUT,
                   help="Per-paper timeout in seconds")
    p.add_argument("--no-prioritise", action="store_true",
                   help="Skip project-bib priority pass (random library order)")
    p.add_argument("--paperpile-json", type=Path, default=DEFAULT_PAPERPILE,
                   help="Path to Paperpile JSON export (or set $PAPERPILE_JSON)")
    p.add_argument("--pdf-root", type=Path, default=DEFAULT_PDF_ROOT,
                   help="Path to local PDF mirror (or set $PAPERPILE_PDF_ROOT)")
    p.add_argument("--projects-root", type=Path, default=DEFAULT_PROJECTS_ROOT,
                   help="Path to research projects tree for bib priority "
                        "(or set $RESEARCH_PROJECTS_ROOT). Pass empty / unset to disable.")
    p.add_argument("--cache-root", type=Path, default=None,
                   help="Override cache root (default: $PDF_EXTRACT_CACHE_DIR or auto)")
    args = p.parse_args()

    if args.paperpile_json is None:
        p.error("paperpile JSON path is required: pass --paperpile-json or set $PAPERPILE_JSON")
    if args.pdf_root is None:
        p.error("PDF root is required: pass --pdf-root or set $PAPERPILE_PDF_ROOT")

    if args.cache_root:
        os.environ["PDF_EXTRACT_CACHE_DIR"] = str(args.cache_root)

    print(f"=== bulk_extract starting at {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"  budget: {args.max_runtime}s wall-clock, {args.max_papers} papers max")
    print(f"  per-paper timeout: {args.per_paper_timeout}s")

    if not args.paperpile_json.exists():
        print(f"ERROR: paperpile JSON not found: {args.paperpile_json}", file=sys.stderr)
        return 1
    library = json.loads(args.paperpile_json.read_text())
    print(f"  library: {len(library)} entries")

    if args.no_prioritise or args.projects_root is None:
        queue = [(e, p) for e in library if (p := resolve_pdf(e, args.pdf_root)) is not None]
        print(f"  queue: {len(queue)} papers (un-prioritised)")
    else:
        queue = prioritise(library, args.pdf_root, args.projects_root)

    # Filter cached-already
    print(f"[scan] filtering already-cached papers...")
    fresh = [(e, p) for e, p in queue if not is_already_cached(p)]
    print(f"[scan] {len(queue) - len(fresh)} already cached, {len(fresh)} to process")

    if not fresh:
        print("nothing to do — library fully extracted")
        return 0

    # Top-level wall-clock budget
    deadline = time.monotonic() + args.max_runtime
    n_done = 0
    n_failed = 0
    n_timeout = 0
    n_skipped = 0

    for entry, pdf_path in fresh[: args.max_papers]:
        if time.monotonic() >= deadline:
            print(f"[budget] wall-clock exhausted; stopping")
            break

        citekey = entry.get("citekey", "?")
        # Per-paper timeout via SIGALRM (Python signals are main-thread only;
        # this script runs in a single thread so it's fine).
        signal.signal(signal.SIGALRM, _alarm_handler)
        signal.alarm(args.per_paper_timeout)
        t0 = time.time()
        try:
            doc = extract(pdf_path, backend="marker", use_cache=True)
            n_done += 1
            print(f"[ok]   {citekey:30}  {time.time()-t0:6.1f}s  "
                  f"md={len(doc.markdown):>7}  figs={len(doc.figures):>2}  "
                  f"tabs={len(doc.tables):>2}  refs={len(doc.references):>3}")
        except TimeBudgetExceeded:
            n_timeout += 1
            print(f"[TIMEOUT] {citekey} after {args.per_paper_timeout}s — skipping")
        except Exception as e:
            n_failed += 1
            print(f"[FAIL]  {citekey:30}  {type(e).__name__}: {str(e)[:120]}")
        finally:
            signal.alarm(0)

    print(f"=== done: {n_done} extracted, {n_failed} failed, {n_timeout} timeout, "
          f"{n_skipped} skipped, {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
