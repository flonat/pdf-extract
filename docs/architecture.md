# Architecture

How `pdf-extract` is organised and why.

## Layered model

```
            ┌─────────────────────────────────────────────────────────────┐
            │                  Public API (__init__.py)                   │
            │     extract()   section()   ExtractedDoc, ExtractedTable    │
            └─────────────────────────────────────────────────────────────┘
                                       │
            ┌──────────────────────────▼──────────────────────────────────┐
            │                       Dispatch                              │
            │   - resolves backend ("auto" | "pymupdf4llm" | "marker")    │
            │   - reads/writes cache                                      │
            │   - normalises return into ExtractedDoc                     │
            └──────────────┬─────────────────────┬────────────────────────┘
                           │                     │
            ┌──────────────▼────────────┐    ┌───▼─────────────────────────┐
            │   backends/pymupdf4llm.py │    │     backends/marker.py      │
            │   - pymupdf4llm.to_md     │    │   - PdfConverter (lazy)     │
            │   - heading-split refs    │    │   - figures → PNG to disk   │
            │   - PDF metadata via      │    │   - tables harvested via    │
            │     pymupdf                │   │     Markdown table regex    │
            └───────────────────────────┘    └─────────────────────────────┘

                           ┌─────────────────────────┐
                           │        cache.py         │
                           │  pdf_sha + backend +    │
                           │  version → directory    │
                           └─────────────────────────┘
```

## Why these boundaries

### Backends are leaf modules

Each backend takes a `Path` and returns an `ExtractedDoc`. They don't know about caching, dispatch policy, or other backends. This means:
- Adding Docling later is a single file plus a `dispatch._extract_docling` branch.
- Marker's heavy ML imports stay lazy — `from pdf_extract import extract` does not load PyTorch.
- Tests can import `backends.pymupdf4llm` directly to bypass dispatch policy.

### Dispatch owns cache

Cache concerns (key construction, invalidation, figure-dir creation) live in one module. Backends never read or write the cache. Reasons:
- Easy to swap cache implementations (e.g., to a database) without touching backends.
- The two backends share the same cache layout so a `pymupdf4llm` ingest and a later `marker` ingest of the same PDF never conflict.
- The `"auto"` policy can compose cache reads across backends (currently it just tries pymupdf4llm first; future variants can be smarter).

### `ExtractedDoc` is the contract

Both backends emit the same dataclass shape. The public API never returns backend-specific objects. This means:
- Consumers can switch backends with an env var (`PAPERPILE_PDF_BACKEND=marker`) — no code changes.
- The cache stores one canonical JSON shape; reading back doesn't need to know which backend wrote it.
- Tests can construct synthetic `ExtractedDoc` instances without touching real PDFs.

## Auto-routing logic

`backend="auto"` does the cheap thing first:

```python
fast = pymupdf4llm.extract(pdf)
if len(fast.markdown) >= 100:        # threshold = _AUTO_FALLBACK_MIN_CHARS
    return fast
try:
    return marker.extract(pdf)
except ImportError:
    fast.metadata["auto_fallback"] = "marker_unavailable"
    return fast
```

**Deliberately simple.** An earlier version also fell back when "Table N" appeared in prose but no Markdown tables were extracted. That heuristic fired on virtually every academic paper (papers reference tables in prose constantly), turning a 3-second auto path into a 60-second one. Removed 2026-04-29.

If a caller genuinely needs structured tables/figures, they should pass `backend="marker"` explicitly. The auto path optimises for "is this PDF readable as text?" — not "could Marker extract more?"

## Cache invalidation

The cache key is:

```
sha1(pdf_bytes)[:16] + "_" + backend + "_v" + EXTRACT_VERSION
```

Three independent invalidation axes:

| Axis | When it bumps | Effect |
|---|---|---|
| `sha1(pdf_bytes)` | The PDF file itself changes | New cache entry; old one orphaned |
| `backend` | Caller asks for a different backend | New cache entry; both coexist |
| `EXTRACT_VERSION` | Output schema changes (e.g., new `ExtractedDoc` field, regex fix) | All old entries become stale; warm reads regenerate naturally on next access |

We do **not** include the marker-pdf version in the key because Marker's output is mostly stable across patch versions, and bumping `EXTRACT_VERSION` is a manual decision (we'd rather keep working caches across cosmetic Marker bumps than churn the entire cache for every minor release).

If a Marker upgrade *does* change output materially, bump `EXTRACT_VERSION` to force regeneration.

## Failure modes (intentional)

| Failure | Behaviour | Rationale |
|---|---|---|
| PDF doesn't exist | `FileNotFoundError` | Fail loud at the API boundary |
| Marker not installed, backend="marker" | `ImportError` from `_ensure_marker` | Tells the caller exactly how to install |
| Marker not installed, backend="auto" + thin pymupdf output | Returns pymupdf result with `metadata["auto_fallback"] = "marker_unavailable"` | Don't break the call; surface the fact |
| Marker hangs on a pathological PDF | The `extract()` call hangs | The bulk cron wraps each call in a per-paper SIGALRM (`scripts/bulk_extract.py`); standalone callers should add their own timeout |
| Cache write fails (disk full) | `OSError` from `cache.save()` | Fail loud |
| Cache read fails (corrupt JSON) | `cache.load()` returns `None` | Treat as miss; regenerate |

## What's deliberately out of scope

- **OCR pre-processing** — both backends handle their own (Marker via Surya, pymupdf via embedded text). We don't add a third layer.
- **PDF repair** — if a PDF is malformed enough that both backends fail, the caller's job is to find a valid PDF.
- **Chunking** — `ExtractedDoc.markdown` is the whole document. Consumers who want chunks should use `section()` or write their own.
- **Re-ranking / search** — pure extraction. Search lives in refpile.
- **OCR fallback for scanned PDFs that pymupdf returns thin text from** — currently the auto path falls back to Marker, which has Surya OCR built in. We rely on Marker for the OCR path; we don't add our own.
