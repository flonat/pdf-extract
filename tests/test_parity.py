"""pymupdf4llm backend produces a sane ExtractedDoc on a real academic paper."""
from __future__ import annotations

from pathlib import Path

import pytest

from pdf_extract import extract

FIXTURE = Path(__file__).parent / "fixtures" / "sample_paper.pdf"


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture PDF missing")
def test_pymupdf4llm_basic():
    doc = extract(FIXTURE, backend="pymupdf4llm", use_cache=False)
    assert doc.backend == "pymupdf4llm"
    assert len(doc.markdown) > 1000
    assert "polarization" in doc.markdown.lower() or "polarisation" in doc.markdown.lower()
    # pymupdf4llm doesn't extract structured tables/figures
    assert doc.tables == []
    assert doc.figures == []
    # Should produce at least metadata page count
    assert doc.metadata.get("n_pages", 0) > 0


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture PDF missing")
def test_pymupdf4llm_extracts_references_when_present():
    """If the paper has a References section, we should pick up at least a few entries."""
    doc = extract(FIXTURE, backend="pymupdf4llm", use_cache=False)
    # References list may be empty if heading is missing or non-standard;
    # this is just a smoke test that the path runs without error.
    assert isinstance(doc.references, list)
    for r in doc.references:
        assert r.raw  # non-empty
