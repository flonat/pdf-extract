# pdf-extract

**Turn academic PDFs into structured Markdown — with tables, figures, equations, references, and metadata — and cache the results so subsequent reads are instant.** Designed for people building RAG systems, AI agents, or literature-review tools over a personal paper library.

```
your.pdf  ──►  extract(backend="auto")  ──►  ExtractedDoc {
                                                markdown,         # full text
                                                tables,           # as Markdown tables
                                                figures,          # as PNG paths
                                                references,       # bibliography
                                                metadata,         # title, authors, n_pages
                                              }
                       │
                       ├─ pymupdf4llm  (fast: ~3s, flattens tables, no figures)
                       └─ marker       (~30-60s/CPU, full structure)
                                       │
                                       └─ on-disk cache: warm reads <50ms
```

## How this fits with `pdf-clean`

| Step | Package | What it does |
|---|---|---|
| 1. Extract | **`pdf-extract`** (this repo) | PDF → structured Markdown + tables + figures + references |
| 2. Clean | [`pdf-clean`](https://github.com/flonat/pdf-clean) | Markdown → deterministically-normalised text (strip running headers, fix hyphenation, ligatures, etc.) |
| 3. Use | your code | Embed for RAG, send to LLM, render in a UI, etc. |

The two packages are deliberately separated: `pdf-extract` does I/O (PDF parsing, cache reads/writes); `pdf-clean` is a pure string-in/string-out function. You can use them together or independently.

### Use them together (recommended for academic papers)

```python
from pdf_extract import extract       # this repo
from pdf_clean   import clean         # https://github.com/flonat/pdf-clean

# 1. Extract — auto-routes to the cheap backend, falls back to Marker if needed.
#    Caches the result on disk so the next call on the same PDF is <50 ms.
doc = extract("paper.pdf", backend="auto")

# 2. Clean — three different profiles for three downstream uses.
#    pdf-clean is pure: no I/O, no LLM calls, deterministic.
text_for_rag    = clean(doc.markdown, profile="embedding")     # → vector index
text_for_llm    = clean(doc.markdown, profile="llm_read")      # → LLM context
text_for_review = clean(doc.markdown, profile="peer_review")   # → review pipeline

# 3. The structured fields don't go through pdf-clean — they're already structured.
for table in doc.tables:
    print(table.markdown)             # already a Markdown table
for fig in doc.figures:
    print(fig.image_path)             # already a PNG on disk
```

The cache stores raw extracted Markdown (pre-clean). One cache entry serves all three `pdf-clean` profiles, so swapping profiles never re-extracts.

Install both: `pip install pdf-extract pdf-clean` (once published; today: `uv pip install -e ../pdf-extract -e ../pdf-clean` from sibling checkouts).

## Why this exists

`pymupdf4llm` is fast (~3 s/PDF) but flattens tables and ignores figures. `marker-pdf` extracts structure but pays a heavy first-call cost (~3-5 GB of models, ~30-60 s/paper on CPU). Most reads only need fast text; some tasks (deep lit-review, section-scoped reads, table extraction) need structure. This package picks the right backend per call, caches the result keyed by `sha1(pdf) + backend + version` so warm reads are <50 ms, and exposes a stable typed return shape to every consumer (programmatic, CLI, MCP server).

> **What's Paperpile?** A cloud-based reference manager popular among academics (alternative to Zotero/Mendeley). It exports your library as a JSON file plus a synced PDF mirror — handy for building local tooling. `pdf-extract` works with any PDF; the optional Paperpile integration just adds bulk-extract priority based on which papers are cited in your active project bibliographies. If you don't use Paperpile, ignore that section — point `pdf-extract` at any directory of PDFs and it works.

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

### MCP integration via `paperpile` (optional)

If you use [Paperpile](https://paperpile.com) (cloud reference manager) as your library, `paperpile` exposes six PDF tools backed by `pdf-extract`. Each takes a Paperpile citekey, resolves the PDF path, calls `extract()`, and (for text/section paths) pipes through `pdf-clean`:

```bash
paperpile get-pdf-text       --citekey Smith2024-ab
paperpile get-pdf-section    --citekey Smith2024-ab --section "Methods"
paperpile get-pdf-tables     --citekey Smith2024-ab
paperpile get-pdf-figures    --citekey Smith2024-ab
paperpile get-pdf-references --citekey Smith2024-ab
paperpile get-pdf-metadata   --citekey Smith2024-ab
```

If you don't use Paperpile, skip this section — the standalone `pdf-extract` CLI works on any PDF path. See [`docs/integration.md`](docs/integration.md) for how the citekey resolution works (it's small enough to copy if you're rolling your own library wrapper).

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

For users with a large library who want to fill the cache progressively in the background: [`scripts/bulk_extract.py`](scripts/bulk_extract.py) walks a Paperpile JSON export, builds a priority queue (papers cited in active project bibliographies first, then recent additions, then long tail), and runs `extract()` until a wall-clock budget is exhausted. Per-paper SIGALRM timeout guards against pathological PDFs (book-length textbooks routinely hang `pymupdf4llm`). Configure via env vars (`PAPERPILE_JSON`, `PAPERPILE_PDF_ROOT`, `RESEARCH_PROJECTS_ROOT`) and run on whatever schedule fits your machine — the macOS launchd plist pattern (`scripts/run_bulk_extract.sh`) is one option. See [`docs/integration.md`](docs/integration.md#bulk-marker-cron).

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
- [`docs/integration.md`](docs/integration.md) — how `paperpile`, the paper-writing container, and the bulk cron consume `pdf-extract`
- [`docs/troubleshooting.md`](docs/troubleshooting.md) — Marker hangs, MPS, cache invalidation, common errors

## Status

v0.1.0 — Phase B of the 2026-04-29 PDF launch. See `Task-Management/docs/plans/2026-04-29-pdf-launch.md` for the full launch plan and Phase A's empirical findings (which embedding model the library should run on, and why).

## License

MIT. Marker is Apache 2.0 (vendored as an optional dep, not redistributed).
