"""extract() dispatch behaviour: cache hit, errors, section()."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from pdf_extract import EXTRACT_VERSION, extract, section
from pdf_extract.models import ExtractedDoc

FIXTURE = Path(__file__).parent / "fixtures" / "sample_paper.pdf"


def test_unknown_backend_raises(tmp_path):
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"%PDF-1.4\n%%EOF")
    with pytest.raises(ValueError):
        extract(fake, backend="rocketship")  # type: ignore


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        extract(tmp_path / "nope.pdf", backend="pymupdf4llm")


def test_docling_not_implemented(tmp_path):
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"%PDF-1.4\n%%EOF")
    with pytest.raises(NotImplementedError):
        extract(fake, backend="docling")


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture PDF missing")
def test_cache_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("PDF_EXTRACT_CACHE_DIR", str(tmp_path))
    # First call writes the cache
    doc1 = extract(FIXTURE, backend="pymupdf4llm", use_cache=True)
    # Cache directory should now exist
    assert any(tmp_path.iterdir())
    # Second call must hit the cache (we verify by mutating the cached file
    # and confirming the second call returns the mutated content)
    from pdf_extract import cache as _cache
    cache_d = _cache.cache_dir(FIXTURE, "pymupdf4llm", EXTRACT_VERSION, root=tmp_path)
    doc_json = cache_d / "doc.json"
    payload = doc_json.read_text()
    mutated = payload.replace(doc1.markdown[:50], "SENTINEL_CACHE_HIT" + doc1.markdown[18:50])
    doc_json.write_text(mutated)
    doc2 = extract(FIXTURE, backend="pymupdf4llm", use_cache=True)
    assert "SENTINEL_CACHE_HIT" in doc2.markdown


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture PDF missing")
def test_no_cache_bypass(tmp_path, monkeypatch):
    monkeypatch.setenv("PDF_EXTRACT_CACHE_DIR", str(tmp_path))
    doc = extract(FIXTURE, backend="pymupdf4llm", use_cache=False)
    # Cache dir should be empty since we passed use_cache=False
    contents = list(tmp_path.iterdir()) if tmp_path.exists() else []
    assert contents == []
    assert doc.backend == "pymupdf4llm"


def test_section_finds_fuzzy_match():
    doc = ExtractedDoc(
        markdown=(
            "# Introduction\n\nIntro body text here.\n\n"
            "## Methods\n\nWe used X.\n\n"
            "## Results\n\nFound Y.\n\n"
            "## Discussion\n\nMeans Z."
        ),
        backend="test",
    )
    methods = section(doc, "methods")
    assert methods is not None
    assert "We used X" in methods
    assert "Found Y" not in methods  # stops at next heading

    # Fuzzy substring match
    intro = section(doc, "introd")
    assert intro is not None
    assert "Intro body" in intro

    # Missing section
    assert section(doc, "References") is None
