# Integration

How `pdf-extract` is consumed across the rest of the workspace.

## Map

```
                        ┌────────────────────────────┐
                        │      pdf-extract           │
                        │  (lib + CLI + Marker)      │
                        └──────────┬─────────────────┘
                                   │
       ┌──────────────────┬────────┴───────────────────────┐
       │                  │                                │
       ▼                  ▼                                ▼
┌──────────────┐   ┌──────────────────┐         ┌─────────────────────┐
│ mcp-paperpile│   │  paper-writing   │         │   bulk_extract.py   │
│  (6 tools)   │   │  container       │         │   (launchd cron)    │
└──────┬───────┘   │  (CLI shim)      │         │   nightly @ 02:00   │
       │           └──────────────────┘         └─────────────────────┘
       ▼
   pdf-clean
  (profile=llm_read)
```

All four consumers share the same on-disk cache at `/Volumes/SSD/pdf-extract-cache/` (or `PDF_EXTRACT_CACHE_DIR`). Once a PDF is extracted by any path, every other path benefits.

## mcp-paperpile (the primary consumer)

`packages/mcp-paperpile/src/paperpile_mcp/library.py` exposes six PDF tools, all backed by `pdf-extract`.

| Tool | Backend used | Why |
|---|---|---|
| `get_pdf_text` | `auto` | Most calls just need text; auto path picks pymupdf4llm and falls through to Marker only if the PDF is suspiciously thin (likely scanned) |
| `get_pdf_section` | `marker` (forced) | Needs Marker's stronger heading structure for fuzzy matching |
| `get_pdf_tables` | `marker` (forced) | Pymupdf flattens tables; only Marker emits Markdown tables |
| `get_pdf_figures` | `marker` (forced) | Pymupdf doesn't extract figures at all |
| `get_pdf_references` | `pymupdf4llm` (forced) | References parsing is a heading split — both backends do it equally well; pymupdf is ~100x faster |
| `get_pdf_metadata` | `pymupdf4llm` (forced) | Metadata is page count + PDF metadata — pymupdf is enough; Marker is overkill |

`get_pdf_text` and `get_pdf_section` additionally pipe through `pdf-clean(profile="llm_read")` for noise removal (running headers, hyphenation, ligatures). The other four don't — table/figure/reference content shouldn't be re-cleaned, and metadata is already structured.

### Wiring

```bash
# In mcp-paperpile's venv:
uv pip install -e ../pdf-extract           # default fast path only
uv pip install -e ../pdf-extract[marker]   # full structure-aware
```

`mcp-paperpile/pyproject.toml` deliberately does NOT pin pdf-extract — it's installed as an editable sibling. This matches how `pdf-clean` is wired and lets local changes propagate without version churn.

## Paper-writing container

`/Volumes/SSD/claude-container/`'s `paper-warm-mcps` recipe copies `pdf-extract` into the container's writable `/home/coder/.pkgs/` and installs it (with `[marker]`) into the paperpile venv. A CLI shim at `/home/coder/.local/bin/pdf-extract` wraps `uv run --project /home/coder/.pkgs/mcp-paperpile pdf-extract`.

The host's cache dir is bind-mounted at `/pdf-extract-cache` with `PDF_EXTRACT_CACHE_DIR=/pdf-extract-cache`. So:

- Mac Mini host paperpile MCP, container paperpile MCP, and container `pdf-extract` CLI all read/write the **same** cache.
- A paper extracted by the bulk cron at 03:00 is instantly available to any container started at 09:00.
- Marker model files (~3-5GB) live in `~/.cache/marker/` on the container; not bind-mounted (each container pays the first download once unless the user mounts).

## Bulk Marker cron

`packages/pdf-extract/scripts/bulk_extract.py` walks the Paperpile JSON export, builds a priority queue (papers cited in active project bibs first, then recent, then long tail), skips already-cached entries, and runs `extract(backend="marker")` until a wall-clock budget is exhausted.

Schedule: nightly at 02:00 local, 6h budget, 120s per-paper SIGALRM timeout. Configurable via env:

| Var | Default | Effect |
|---|---|---|
| `PDF_EXTRACT_MAX_RUNTIME` | `21600` (6h) | Total wall-clock budget per run |
| `PDF_EXTRACT_MAX_PAPERS` | `2000` | Hard cap on papers per run |
| `PDF_EXTRACT_CACHE_DIR` | `/Volumes/SSD/pdf-extract-cache` | Where extracts go |

Logs land at `packages/pdf-extract/log/bulk-extract-YYYYMMDD-HHMMSS.log`. Each line:

| Prefix | Meaning |
|---|---|
| `[ok]` | Paper extracted successfully |
| `[FAIL]` | Marker raised an exception |
| `[TIMEOUT]` | Per-paper budget exhausted (typically book-length PDFs or math-heavy edge cases) |
| `[budget]` | Wall-clock budget exhausted; cron is stopping |

A 2-week followup cron (`com.flonat.pdf-extract-bulk-2week-check`, fires once on 2026-05-13) emails a progress report. See [`scripts/pdf-extract-bulk-2week-check.sh`](../../../scripts/pdf-extract-bulk-2week-check.sh).

## Composition with pdf-clean

`pdf-extract` doesn't run `pdf-clean` itself — separation of concerns. Callers compose:

```python
from pdf_extract import extract
from pdf_clean import clean

doc = extract("paper.pdf", backend="auto")
md_for_llm    = clean(doc.markdown, profile="llm_read")
md_for_embed  = clean(doc.markdown, profile="embedding")
md_for_review = clean(doc.markdown, profile="peer_review")
```

`pdf-clean` is pure (no I/O). `pdf-extract` does I/O at the dispatch layer but the backends are also pure-ish (just PDF input → ExtractedDoc output). Cache hits never touch pdf-clean — the markdown is stored post-extraction, pre-clean. This is deliberate so a single cache entry can serve all three pdf-clean profiles.

## Adding a new consumer

The minimum integration is:

```python
from pdf_extract import extract
doc = extract(my_pdf_path, backend="auto")
do_something_with(doc.markdown)
```

If your consumer wants Marker without the full venv installation:

```python
try:
    doc = extract(my_pdf_path, backend="marker")
except ImportError:
    # Fall back to pymupdf4llm
    doc = extract(my_pdf_path, backend="pymupdf4llm")
```

Or just use `backend="auto"` and check `doc.metadata.get("auto_fallback")` to detect when Marker would have been used but wasn't installed.

For long-running consumers (servers, daemons), wrap `extract()` calls with a timeout if you can't tolerate Marker hangs. The library doesn't add its own timeout — the bulk cron does this with SIGALRM at the script level.
