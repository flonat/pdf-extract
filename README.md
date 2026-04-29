# pdf-extract

Structure-aware PDF extraction with an on-disk cache. Routes to `pymupdf4llm` (fast path) or `marker-pdf` (structure-aware) per backend choice. Stable typed return shape, cache-aware composition with `pdf-clean`, MCP + CLI surfaces.

```
PDF ──► extract(backend="auto") ──► ExtractedDoc { markdown, tables, figures, references, metadata }
                │
                ├─ pymupdf4llm  (fast: ~3s, flattens tables, no figures)
                └─ marker       (slow: ~30-60s/CPU, tables as Markdown, figures as PNG, LaTeX equations)
                                │
                                └─ on-disk cache keyed by sha1(pdf) + backend + version
```

## Why

`pymupdf4llm` is fast but flattens tables and ignores figures. `marker-pdf` extracts structure but pays a heavy first-call cost (~3-5GB of models, ~30-60s/paper on CPU). Most reads only need fast text; some downstream tasks (deep lit-review, section-scoped reads, table extraction) need structure. This package picks the right backend per call, caches the result so warm reads are instant, and exposes a stable API to multiple consumers.

`pdf-clean` runs orthogonally on the markdown output — see [`docs/integration.md`](docs/integration.md).

## Install

```bash
# Default — fast path only (pymupdf4llm)
uv pip install -e packages/pdf-extract

# With Marker for structure-aware extraction (~3-5GB of ML models on first run)
uv pip install -e 'packages/pdf-extract[marker]'
```

## Use

### Programmatic

```python
from pdf_extract import extract, section

doc = extract("paper.pdf", backend="auto")
# doc.markdown        — full Markdown
# doc.tables          — list[ExtractedTable]   (Markdown + caption + page)
# doc.figures         — list[ExtractedFigure]  (image_path + caption + page)
# doc.references      — list[ExtractedReference] (raw bib strings)
# doc.metadata        — {title, authors, n_pages, ...}
# doc.backend         — "pymupdf4llm" | "marker"

methods = section(doc, "Methods")  # fuzzy substring match on headings
```

| Backend | What it returns | Cold cost | Warm cost | When to use |
|---|---|---|---|---|
| `"pymupdf4llm"` | Markdown text (tables flattened, no figures) | ~3s | <50ms | Speed > structure |
| `"marker"` | Markdown + tables + figure PNGs + LaTeX equations | ~30-60s | <50ms | Need structure |
| `"auto"` (default) | pymupdf4llm if text ≥100 chars, else Marker fallback | ~3s typical | <50ms | Default for general use |
| `"docling"` | reserved | — | — | v0.2 |

### CLI

```bash
pdf-extract paper.pdf                          # full Markdown to stdout
pdf-extract paper.pdf --section "Methods"      # one section by fuzzy heading match
pdf-extract paper.pdf --tables                 # all tables (Markdown)
pdf-extract paper.pdf --figures                # figure paths + captions
pdf-extract paper.pdf --references             # bibliography entries
pdf-extract paper.pdf --metadata               # JSON dict
pdf-extract paper.pdf --json                   # full ExtractedDoc as JSON
pdf-extract paper.pdf --backend marker         # force backend
pdf-extract paper.pdf --no-cache               # bypass cache
```

### MCP (via `mcp-paperpile`)

Six PDF tools auto-derived from the registry, all backed by `pdf-extract`:

```bash
paperpile get-pdf-text       --citekey Smith2024-ab
paperpile get-pdf-section    --citekey Smith2024-ab --section "Methods"
paperpile get-pdf-tables     --citekey Smith2024-ab
paperpile get-pdf-figures    --citekey Smith2024-ab
paperpile get-pdf-references --citekey Smith2024-ab
paperpile get-pdf-metadata   --citekey Smith2024-ab
```

The `mcp-paperpile` library resolves the citekey to a PDF path, calls `extract()`, and (for text/section paths) pipes through `pdf-clean` profile=`llm_read`. See [`docs/integration.md`](docs/integration.md).

## Cache

| Machine | Default location |
|---|---|
| Mac Mini (SSD present) | `/Volumes/SSD/pdf-extract-cache/` |
| Other | `~/.cache/pdf-extract/` |
| Override | `PDF_EXTRACT_CACHE_DIR=...` |

Layout per cached PDF:

```
<cache_root>/<sha>_<backend>_v<version>/
    doc.json          serialised ExtractedDoc
    figures/          extracted figure PNGs (Marker only)
```

Cache key: `sha1(pdf_bytes)[:16] + "_" + backend + "_v" + EXTRACT_VERSION`. Bumping `EXTRACT_VERSION` (in `dispatch.py`) invalidates the entire cache. Backend swaps don't collide — `<sha>_pymupdf4llm_v0.1.0` and `<sha>_marker_v0.1.0` coexist.

## Bulk extraction

A nightly launchd cron (`com.flonat.pdf-extract-bulk`, `02:00`, 6h budget) progressively extracts the Paperpile library with Marker. See [`scripts/bulk_extract.py`](scripts/bulk_extract.py) and [`docs/integration.md`](docs/integration.md#bulk-marker-cron).

## Performance reference

Benchmarks from Mac Mini M4, CPU only, on a typical 18-page Annual Review paper (Iyengar 2019):

| Operation | Time |
|---|---|
| pymupdf4llm cold | ~3s |
| pymupdf4llm warm (cache hit) | <50ms |
| Marker first-ever call (model download + warmup + extraction) | ~537s |
| Marker cold (warmed-up models, fresh PDF) | ~30-60s |
| Marker warm (cache hit) | <50ms |

`docs/troubleshooting.md` covers Marker hangs on math-heavy / book-length PDFs and the `--per-paper-timeout` knob.

## Docs

- [`docs/architecture.md`](docs/architecture.md) — backends, dispatch, cache, why each design choice
- [`docs/integration.md`](docs/integration.md) — how `mcp-paperpile`, the paper-writing container, and the bulk cron consume `pdf-extract`
- [`docs/troubleshooting.md`](docs/troubleshooting.md) — Marker hangs, MPS, cache invalidation, common errors

## Status

v0.1.0 — Phase B of the 2026-04-29 PDF launch. See `Task-Management/docs/plans/2026-04-29-pdf-launch.md` for the full launch plan and Phase A's empirical findings (which embedding model the library should run on, and why).

## License

MIT. Marker is Apache 2.0 (vendored as an optional dep, not redistributed).
