# pdf-extract

Structure-aware PDF extraction with on-disk cache. Routes to `pymupdf4llm` (fast path) or `marker-pdf` (structure-aware) per backend choice.

## Why

`pymupdf4llm` is fast but flattens tables and ignores figures. Marker is slow but extracts tables as Markdown, figures as PNGs, and equations as LaTeX. This package picks the right backend per call, caches results SHA+backend+version-keyed, and exposes a stable API to consumers (paperpile MCP, paper-writing container, scripts).

`pdf-clean` runs orthogonally — pass `extract(...).markdown` through `clean()` for the embedding/LLM-read profiles.

## Install

```bash
# Default (fast path only)
uv pip install -e packages/pdf-extract

# With Marker for structure-aware extraction
uv pip install -e 'packages/pdf-extract[marker]'
```

## Use

### Programmatic

```python
from pdf_extract import extract

doc = extract("paper.pdf", backend="auto")
# doc.markdown        — full Markdown
# doc.tables          — list[ExtractedTable]
# doc.figures         — list[ExtractedFigure]
# doc.references      — list[ExtractedReference]
# doc.metadata        — {title, authors, year, abstract, n_pages}
# doc.backend         — which backend was used
```

Backends:
- `"pymupdf4llm"` — fast path, no tables/figures (default for auto when text looks complete)
- `"marker"` — structure-aware (auto fallback when pymupdf4llm output is too short or mentions tables it didn't extract)
- `"auto"` (default) — pymupdf4llm first, Marker fallback if installed
- `"docling"` — reserved for v0.2

Section-scoped reads:

```python
from pdf_extract import extract, section
doc = extract("paper.pdf", backend="marker")
methods = section(doc, "Methods")  # fuzzy substring match on headings
```

### CLI

```bash
pdf-extract paper.pdf                          # full Markdown to stdout
pdf-extract paper.pdf --section "Methods"      # one section
pdf-extract paper.pdf --tables                 # all tables
pdf-extract paper.pdf --figures                # figure paths + captions
pdf-extract paper.pdf --references             # bibliography
pdf-extract paper.pdf --metadata               # JSON dict
pdf-extract paper.pdf --json                   # full ExtractedDoc as JSON
pdf-extract paper.pdf --backend marker         # force backend
pdf-extract paper.pdf --no-cache               # bypass cache
```

## Cache

| Machine | Default location |
|---|---|
| Mac Mini (SSD present) | `/Volumes/SSD/pdf-extract-cache/` |
| Other | `~/.cache/pdf-extract/` |

Override with `PDF_EXTRACT_CACHE_DIR=…`.

Cache key: `sha1(pdf_bytes)[:16]_<backend>_v<extract_version>`. Bumping `EXTRACT_VERSION` invalidates the entire cache.

## Status

v0.1.0 — Phase B of the 2026-04-29 PDF launch. See `docs/plans/2026-04-29-pdf-launch.md`.
