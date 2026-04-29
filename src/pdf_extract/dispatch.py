"""extract() — top-level dispatch with cache.

backend="auto" picks pymupdf4llm first (cheap), falls back to Marker only if
the result is suspiciously thin (likely scanned PDF or extraction failure)
AND the [marker] extra is installed.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from . import cache as _cache
from .models import ExtractedDoc, ExtractedReference

EXTRACT_VERSION = "0.1.0"

Backend = Literal["auto", "pymupdf4llm", "marker", "docling"]

_AUTO_FALLBACK_MIN_CHARS = 100  # below this, pymupdf4llm output is unusable
_AUTO_TABLE_HINT = re.compile(r"\b(table|tbl\.?)\s*\d+", re.IGNORECASE)


def extract(
    pdf_path: Path | str,
    backend: Backend = "auto",
    figures_dir: Path | None = None,
    skip_ocr: bool = False,
    use_cache: bool = True,
) -> ExtractedDoc:
    """Extract structured content from a PDF.

    Parameters
    ----------
    pdf_path: PDF file to process.
    backend: 'auto' (default), 'pymupdf4llm', 'marker', or 'docling' (reserved).
    figures_dir: where Marker writes figure PNGs. Defaults to per-cache figures dir.
    skip_ocr: pass through to Marker; ignored by pymupdf4llm.
    use_cache: read/write the on-disk cache (sha+backend+version keyed).
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    if backend == "auto":
        return _extract_auto(pdf_path, figures_dir, skip_ocr, use_cache)

    if backend == "pymupdf4llm":
        return _extract_pymupdf(pdf_path, use_cache)

    if backend == "marker":
        return _extract_marker(pdf_path, figures_dir, skip_ocr, use_cache)

    if backend == "docling":
        raise NotImplementedError("docling backend not implemented in v0.1.0")

    raise ValueError(f"unknown backend: {backend!r}")


def _extract_pymupdf(pdf_path: Path, use_cache: bool) -> ExtractedDoc:
    if use_cache:
        cached = _cache.load(pdf_path, "pymupdf4llm", EXTRACT_VERSION)
        if cached is not None:
            return cached
    from .backends import pymupdf4llm as bk
    doc = bk.extract(pdf_path)
    if use_cache:
        _cache.save(pdf_path, doc, "pymupdf4llm", EXTRACT_VERSION)
    return doc


def _extract_marker(pdf_path: Path, figures_dir: Path | None,
                    skip_ocr: bool, use_cache: bool) -> ExtractedDoc:
    if use_cache:
        cached = _cache.load(pdf_path, "marker", EXTRACT_VERSION)
        if cached is not None:
            return cached
    if figures_dir is None:
        figures_dir = _cache.figures_dir(pdf_path, "marker", EXTRACT_VERSION)
    figures_dir.mkdir(parents=True, exist_ok=True)
    from .backends import marker as bk
    doc = bk.extract(pdf_path, figures_dir=figures_dir, skip_ocr=skip_ocr)
    if use_cache:
        _cache.save(pdf_path, doc, "marker", EXTRACT_VERSION)
    return doc


def _extract_auto(pdf_path: Path, figures_dir: Path | None,
                  skip_ocr: bool, use_cache: bool) -> ExtractedDoc:
    """Try pymupdf4llm first; fall back to Marker if (a) text is too short
    (likely scanned), or (b) markdown mentions tables but extracted none."""
    fast = _extract_pymupdf(pdf_path, use_cache)
    needs_fallback = (
        len(fast.markdown) < _AUTO_FALLBACK_MIN_CHARS
        or (_AUTO_TABLE_HINT.search(fast.markdown) and not fast.tables)
    )
    if not needs_fallback:
        return fast
    try:
        return _extract_marker(pdf_path, figures_dir, skip_ocr, use_cache)
    except ImportError:
        # Marker not installed — return fast result with a note in metadata
        fast.metadata["auto_fallback"] = "marker_unavailable"
        return fast


# --- Convenience: section-scoped read ---

_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def section(doc: ExtractedDoc, name: str) -> str | None:
    """Return the markdown for a section whose heading fuzzily matches ``name``.

    Matching: case-insensitive substring on the heading text. Returns the body
    text (everything until the next equal-or-higher heading) or None if no match.
    """
    name_lower = name.lower().strip()
    matches = list(_HEADING.finditer(doc.markdown))
    for i, m in enumerate(matches):
        if name_lower in m.group(2).lower():
            depth = len(m.group(1))
            start = m.end()
            end = len(doc.markdown)
            for next_m in matches[i + 1:]:
                if len(next_m.group(1)) <= depth:
                    end = next_m.start()
                    break
            return doc.markdown[start:end].strip()
    return None
