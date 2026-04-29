"""Marker backend — structure-aware extraction (tables, figures, equations).

Lazy-imported. Raises ImportError with a helpful message if [marker] extra
isn't installed. Heavy: first call loads ~3-5 GB of models.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..models import ExtractedDoc, ExtractedFigure, ExtractedReference, ExtractedTable

_TABLE_BLOCK = re.compile(
    r"(?:\n|^)((?:\|[^\n]*\|\n)(?:\|[\s:|-]+\|\n)(?:\|[^\n]*\|\n)+)",
    re.MULTILINE,
)
_REFS_HEADING = re.compile(
    r"^\s{0,3}(?:#{1,6})\s+\**\s*(references|bibliography|works\s+cited|literature\s+cited)\s*\**\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _ensure_marker():
    try:
        import marker  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "Marker backend requires the [marker] extra:\n"
            "  uv pip install -e 'packages/pdf-extract[marker]'\n"
            "or\n"
            "  uv pip install marker-pdf"
        ) from e


def _harvest_tables(md: str) -> list[ExtractedTable]:
    out: list[ExtractedTable] = []
    for i, m in enumerate(_TABLE_BLOCK.finditer(md)):
        # Try to find a caption — typically the line above the table
        start = m.start()
        prefix = md[max(0, start - 300):start].rstrip()
        cap = None
        for line in reversed(prefix.split("\n")):
            line = line.strip()
            if line and re.match(r"^(table\s+\d+[.:]|tbl\.?\s*\d+)", line, re.IGNORECASE):
                cap = line
                break
        out.append(ExtractedTable(
            index=i,
            markdown=m.group(1).strip(),
            caption=cap,
            page=0,  # Marker doesn't always preserve page in the markdown
        ))
    return out


def _split_references(md: str) -> tuple[str, list[str]]:
    m = _REFS_HEADING.search(md)
    if not m:
        return md, []
    body = md[: m.start()].rstrip()
    refs_section = md[m.end():].strip()
    raw_refs = [line.strip() for line in refs_section.split("\n") if line.strip()]
    refs = [r for r in raw_refs if len(r) > 20 and not r.startswith("#")]
    return body, refs


def _harvest_figures(images: dict, figures_dir: Path) -> list[ExtractedFigure]:
    """Persist Marker's image dict to disk; return ExtractedFigure list."""
    out: list[ExtractedFigure] = []
    for i, (img_name, img_bytes) in enumerate(sorted((images or {}).items())):
        # img_name is something like "page_3_image_1.png"; we keep marker's name
        target = figures_dir / img_name
        try:
            if isinstance(img_bytes, (bytes, bytearray)):
                target.write_bytes(bytes(img_bytes))
            else:
                # marker may already give us a PIL image
                img_bytes.save(target)
        except Exception:
            continue
        # Try to extract page number from filename
        page = 0
        m = re.match(r"page[_-]?(\d+)", img_name)
        if m:
            try:
                page = int(m.group(1))
            except ValueError:
                page = 0
        out.append(ExtractedFigure(
            index=i,
            image_path=target,
            caption=None,  # Marker doesn't bind captions to images cleanly
            page=page,
        ))
    return out


def _read_pdf_metadata(pdf_path: Path) -> dict:
    """Use pymupdf for reliable n_pages + title (Marker's metadata is uneven)."""
    try:
        import pymupdf  # type: ignore
        with pymupdf.open(str(pdf_path)) as doc:
            md = doc.metadata or {}
            return {
                "title": (md.get("title") or "").strip(),
                "authors": (md.get("author") or "").strip(),
                "n_pages": doc.page_count,
            }
    except Exception:
        return {"title": "", "authors": "", "n_pages": 0}


def extract(pdf_path: Path, figures_dir: Path, skip_ocr: bool = False) -> ExtractedDoc:
    _ensure_marker()
    # Marker's API surface has shifted across versions; use the high-level
    # converter which is the documented stable entrypoint as of marker-pdf>=1.0.
    from marker.converters.pdf import PdfConverter  # type: ignore
    from marker.models import create_model_dict  # type: ignore
    from marker.output import text_from_rendered  # type: ignore

    converter = PdfConverter(artifact_dict=create_model_dict())
    rendered = converter(str(pdf_path))
    md, _meta, images = text_from_rendered(rendered)

    body, refs = _split_references(md)
    tables = _harvest_tables(body)
    figures = _harvest_figures(images or {}, figures_dir)

    metadata = _read_pdf_metadata(pdf_path)
    if isinstance(_meta, dict):
        # Marker's analysis metadata can include language / parsed title
        if not metadata["title"] and _meta.get("title"):
            metadata["title"] = str(_meta["title"]).strip()

    return ExtractedDoc(
        markdown=body,
        figures=figures,
        tables=tables,
        references=[ExtractedReference(raw=r) for r in refs],
        metadata=metadata,
        backend="marker",
    )
