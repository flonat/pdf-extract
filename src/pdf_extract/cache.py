"""On-disk SHA-keyed cache for extracted documents.

Cache key = sha1(pdf_bytes)[:16] + '_' + backend + '_v' + extract_version

Layout:
  <cache_root>/<sha>_<backend>_v<version>/
    doc.json          serialised ExtractedDoc
    figures/          PNG files referenced by ExtractedFigure.image_path

Invalidation: bump extract_version in __init__.py to invalidate all old entries.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from .models import ExtractedDoc


def _default_cache_root() -> Path:
    """Per-machine cache root.

    Mac Mini: /Volumes/SSD/pdf-extract-cache (high-capacity SSD).
    Other:    ~/.cache/pdf-extract (XDG-style).

    Override via PDF_EXTRACT_CACHE_DIR env var.
    """
    env = os.environ.get("PDF_EXTRACT_CACHE_DIR")
    if env:
        return Path(env)
    ssd = Path("/Volumes/SSD/pdf-extract-cache")
    if ssd.parent.exists():
        return ssd
    return Path.home() / ".cache" / "pdf-extract"


def pdf_sha(pdf_path: Path) -> str:
    h = hashlib.sha1()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def cache_dir(pdf_path: Path, backend: str, version: str, root: Path | None = None) -> Path:
    root = root or _default_cache_root()
    return root / f"{pdf_sha(pdf_path)}_{backend}_v{version}"


def load(pdf_path: Path, backend: str, version: str, root: Path | None = None) -> ExtractedDoc | None:
    d = cache_dir(pdf_path, backend, version, root)
    doc_json = d / "doc.json"
    if not doc_json.exists():
        return None
    try:
        return ExtractedDoc.from_dict(json.loads(doc_json.read_text()))
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def save(pdf_path: Path, doc: ExtractedDoc, backend: str, version: str,
         root: Path | None = None) -> Path:
    """Persist doc.json. Caller is responsible for writing figure PNGs into the
    same directory before calling save() — figures_dir() returns that path."""
    d = cache_dir(pdf_path, backend, version, root)
    d.mkdir(parents=True, exist_ok=True)
    (d / "doc.json").write_text(json.dumps(doc.to_dict(), indent=2))
    return d


def figures_dir(pdf_path: Path, backend: str, version: str, root: Path | None = None) -> Path:
    """Where Marker should write extracted figure PNGs for a given PDF."""
    d = cache_dir(pdf_path, backend, version, root)
    figs = d / "figures"
    figs.mkdir(parents=True, exist_ok=True)
    return figs


def evict(pdf_path: Path, backend: str, version: str, root: Path | None = None) -> bool:
    d = cache_dir(pdf_path, backend, version, root)
    if d.exists():
        shutil.rmtree(d)
        return True
    return False
