"""Data classes returned by extract().

Backend-agnostic — both pymupdf4llm and Marker normalise their output into these.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ExtractedFigure:
    index: int
    image_path: Path | None  # None when extractor didn't save an image (pymupdf4llm)
    caption: str | None
    page: int
    bbox: tuple[float, float, float, float] | None = None
    kind: str | None = None  # "chart" | "photo" | "diagram" — Docling-only


@dataclass
class ExtractedTable:
    index: int
    markdown: str  # | a | b |\n|---|---|\n| 1 | 2 |
    caption: str | None
    page: int


@dataclass
class ExtractedReference:
    raw: str  # original bibliography entry as text


@dataclass
class ExtractedDoc:
    markdown: str
    figures: list[ExtractedFigure] = field(default_factory=list)
    tables: list[ExtractedTable] = field(default_factory=list)
    references: list[ExtractedReference] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    backend: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Path objects don't survive json round-trip — stringify
        for fig in d.get("figures", []):
            if fig.get("image_path") is not None:
                fig["image_path"] = str(fig["image_path"])
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExtractedDoc":
        figures = [
            ExtractedFigure(
                index=f["index"],
                image_path=Path(f["image_path"]) if f.get("image_path") else None,
                caption=f.get("caption"),
                page=f["page"],
                bbox=tuple(f["bbox"]) if f.get("bbox") else None,
                kind=f.get("kind"),
            )
            for f in d.get("figures", [])
        ]
        tables = [ExtractedTable(**t) for t in d.get("tables", [])]
        references = [ExtractedReference(**r) for r in d.get("references", [])]
        return cls(
            markdown=d.get("markdown", ""),
            figures=figures,
            tables=tables,
            references=references,
            metadata=d.get("metadata", {}),
            backend=d.get("backend", ""),
        )
