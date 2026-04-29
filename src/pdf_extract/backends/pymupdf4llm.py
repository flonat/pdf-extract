"""pymupdf4llm backend — fast path, no figures, no structured tables.

Parity wrapper preserving the existing refpile/paperpile behaviour. Sets
backend='pymupdf4llm' on the ExtractedDoc; tables/figures lists stay empty.
References are heuristically extracted via a "References" / "Bibliography"
heading split on the markdown.
"""
from __future__ import annotations

import re
from pathlib import Path

import pymupdf  # bundled with pymupdf4llm
import pymupdf4llm

from ..models import ExtractedDoc, ExtractedReference

_REFS_HEADING = re.compile(
    r"^\s{0,3}(?:#{1,6})\s+\**\s*(references|bibliography|works\s+cited|literature\s+cited)\s*\**\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_REF_LINE = re.compile(r"^\s*(?:[-*]\s+|\d+\.\s+|\[\d+\]\s+)?(.+)$", re.MULTILINE)


def _split_references(md: str) -> tuple[str, list[str]]:
    """Return (body_without_refs, refs_list).

    Heuristic: find the first 'References'/'Bibliography' heading; everything
    after is the bibliography. Each non-empty line becomes one reference entry.
    """
    m = _REFS_HEADING.search(md)
    if not m:
        return md, []
    body = md[: m.start()].rstrip()
    refs_section = md[m.end():].strip()
    if not refs_section:
        return body, []
    raw_refs = [line.strip() for line in refs_section.split("\n") if line.strip()]
    # Skip lines that are clearly not references (sub-headings, page breaks)
    refs = [r for r in raw_refs if len(r) > 20 and not r.startswith("#")]
    return body, refs


def _read_metadata(pdf_path: Path) -> dict:
    try:
        with pymupdf.open(str(pdf_path)) as doc:
            md = doc.metadata or {}
            return {
                "title": (md.get("title") or "").strip(),
                "authors": (md.get("author") or "").strip(),
                "year": "",  # pymupdf doesn't expose year reliably
                "abstract": "",  # not in PDF metadata
                "n_pages": doc.page_count,
            }
    except Exception:
        return {"title": "", "authors": "", "year": "", "abstract": "", "n_pages": 0}


def extract(pdf_path: Path) -> ExtractedDoc:
    md = pymupdf4llm.to_markdown(str(pdf_path))
    body, refs = _split_references(md)
    return ExtractedDoc(
        markdown=body,
        figures=[],
        tables=[],
        references=[ExtractedReference(raw=r) for r in refs],
        metadata=_read_metadata(pdf_path),
        backend="pymupdf4llm",
    )
