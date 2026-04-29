"""Cache: deterministic key, round-trip serialisation, eviction."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf_extract import cache as _cache
from pdf_extract.models import ExtractedDoc, ExtractedFigure, ExtractedTable

FIXTURE = Path(__file__).parent / "fixtures" / "sample_paper.pdf"


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture PDF missing")
def test_pdf_sha_deterministic():
    a = _cache.pdf_sha(FIXTURE)
    b = _cache.pdf_sha(FIXTURE)
    assert a == b
    assert len(a) == 16
    assert all(c in "0123456789abcdef" for c in a)


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture PDF missing")
def test_cache_dir_includes_backend_and_version(tmp_path):
    a = _cache.cache_dir(FIXTURE, "pymupdf4llm", "0.1.0", root=tmp_path)
    b = _cache.cache_dir(FIXTURE, "marker", "0.1.0", root=tmp_path)
    c = _cache.cache_dir(FIXTURE, "pymupdf4llm", "0.2.0", root=tmp_path)
    assert a != b  # different backend → different dir
    assert a != c  # different version → different dir
    assert a.parent == tmp_path


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture PDF missing")
def test_save_load_roundtrip(tmp_path):
    doc = ExtractedDoc(
        markdown="# Hello\n\nWorld.",
        figures=[ExtractedFigure(index=0, image_path=tmp_path / "fig0.png",
                                 caption="Figure 1: x", page=3, bbox=(0.1, 0.2, 0.3, 0.4))],
        tables=[ExtractedTable(index=0, markdown="| a | b |", caption=None, page=2)],
        metadata={"n_pages": 5, "title": "Test"},
        backend="pymupdf4llm",
    )
    _cache.save(FIXTURE, doc, "pymupdf4llm", "0.1.0", root=tmp_path)

    loaded = _cache.load(FIXTURE, "pymupdf4llm", "0.1.0", root=tmp_path)
    assert loaded is not None
    assert loaded.markdown == doc.markdown
    assert loaded.tables[0].markdown == "| a | b |"
    assert loaded.figures[0].page == 3
    assert loaded.figures[0].image_path == tmp_path / "fig0.png"
    assert loaded.metadata["n_pages"] == 5


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture PDF missing")
def test_load_returns_none_on_miss(tmp_path):
    assert _cache.load(FIXTURE, "pymupdf4llm", "9.9.9", root=tmp_path) is None


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture PDF missing")
def test_load_returns_none_on_corrupt_json(tmp_path):
    d = _cache.cache_dir(FIXTURE, "pymupdf4llm", "0.1.0", root=tmp_path)
    d.mkdir(parents=True)
    (d / "doc.json").write_text("{not valid json")
    assert _cache.load(FIXTURE, "pymupdf4llm", "0.1.0", root=tmp_path) is None


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture PDF missing")
def test_evict(tmp_path):
    doc = ExtractedDoc(markdown="x", backend="pymupdf4llm")
    _cache.save(FIXTURE, doc, "pymupdf4llm", "0.1.0", root=tmp_path)
    assert _cache.evict(FIXTURE, "pymupdf4llm", "0.1.0", root=tmp_path)
    assert _cache.load(FIXTURE, "pymupdf4llm", "0.1.0", root=tmp_path) is None
    # Evicting again returns False (nothing to remove)
    assert not _cache.evict(FIXTURE, "pymupdf4llm", "0.1.0", root=tmp_path)
