# Troubleshooting

Real failure modes hit during pdf-extract development + bulk extraction.

## Marker hangs on a specific PDF

**Symptom:** `extract(pdf, backend="marker")` runs at 100% CPU forever, never returns. Often on book-length PDFs (>200 pages) or math-heavy textbooks.

**Cause:** pymupdf4llm (Marker's text-extraction layer) can hang on certain PDF structures. We've seen this on:
- Wager & Athey 2015 (causal forests, math-heavy)
- Vidyasagar 2013 (470-page neural networks textbook)
- Statistical Learning textbooks (James et al. 2023, etc.)
- Long history books exported as PDF (Darwin 2008, Saez & Zucman 2020)

**Fix in the bulk cron:** `scripts/bulk_extract.py` wraps each `extract()` in a SIGALRM timeout (default 120s). On timeout, the script logs `[TIMEOUT] <citekey>` and moves on. Tune via `--per-paper-timeout`.

**Fix for ad-hoc calls:** wrap your call in a subprocess-based timeout (signals don't always interrupt C extensions cleanly):

```python
import subprocess, sys
result = subprocess.run(
    [sys.executable, "-m", "pdf_extract", str(pdf_path), "--backend", "marker"],
    capture_output=True, text=True, timeout=120,
)
```

**Why we don't add a built-in timeout:** signal-based timeouts don't reliably interrupt PyMuPDF's C code. A subprocess-based timeout would force every call to spawn a Python interpreter, which is unacceptable for warm reads. Callers who can't tolerate hangs should use the subprocess pattern themselves.

## "Marker not installed" but I installed it

**Symptom:** `ImportError: Marker backend requires the [marker] extra` even though `marker-pdf` is in your pyproject.toml.

**Cause:** the venv `pdf-extract` is using doesn't have marker. Check which interpreter is running:

```python
import pdf_extract, sys
print(sys.executable)
```

In the workspace, multiple venvs exist:
- `packages/pdf-extract/.venv/` — has marker (after `uv pip install -e '.[marker]'`)
- `packages/paperpile/.venv/` — has marker (after wiring step)
- `packages/refpile/.venv/` — does NOT have marker (refpile uses pymupdf4llm only)

**Fix:** install the extra in the right venv:

```bash
cd <consuming-package>
uv pip install -e '../pdf-extract[marker]'
```

## First Marker call takes 5+ minutes

**Symptom:** First-ever `extract(..., backend="marker")` call after install hangs for 5-10 minutes with no output.

**Cause:** Marker downloads ~3-5GB of ML models (Surya layout, OCR, table-rec, math-rec) on first use. Models cache to `~/.cache/datalab/models/`.

**Fix:** none — this is a one-time download. Subsequent first calls in a fresh process load cached models in ~30s.

**Workaround for fresh machines:** pre-warm by running an extract on any small PDF before you need real performance. The container's `paper-warm-mcps` recipe does this implicitly via `pdf-extract --version`.

## "ModuleNotFoundError: No module named 'pdf_clean'" in paperpile

**Symptom:** Calling `paperpile get-pdf-text` returns "Error: pdf-extract not installed" or similar.

**Cause:** sibling-package wiring isn't always automatic with `uv sync`. After a fresh checkout or `uv lock` regen:

```bash
cd packages/paperpile
uv pip install -e ../pdf-clean
uv pip install -e ../pdf-extract[marker]
```

**Why it happens:** `pyproject.toml` doesn't pin sibling packages (intentionally — keeps the workspace flexible), so editable installs need to be re-run after dependency changes.

## Cache hit when I expected a miss

**Symptom:** I changed `pdf-clean` settings or upgraded `pymupdf4llm` and `extract()` returns the old result.

**Cause:** the cache key only includes `(sha1(pdf), backend, EXTRACT_VERSION)`. Changes to `pdf-clean` don't invalidate it (because cleaning is downstream of extract). Changes to pymupdf4llm don't invalidate it either.

**Fix:** bump `EXTRACT_VERSION` in `src/pdf_extract/dispatch.py` and re-extract. All callers will see the new version on next call.

**Alternative (one-off):** evict a single PDF's cache:

```python
from pdf_extract import cache
from pathlib import Path
cache.evict(Path("paper.pdf"), "marker", "0.1.0")
```

## Disk fills up

**Symptom:** `/Volumes/SSD/pdf-extract-cache/` grows beyond expected size.

**Reference numbers:** ~25k papers × ~5 figures × ~200KB ≈ 25GB. JSON docs are typically 50-100KB each → another ~2GB. Realistic ceiling: ~30GB.

**Fix:** if you need to reclaim space, evict by mtime:

```bash
# Drop entries not accessed in 60 days
find /Volumes/SSD/pdf-extract-cache -maxdepth 1 -type d \
     -mtime +60 -name "*_marker_v*" -exec rm -rf {} +
```

The bulk cron will lazily re-extract them on demand.

## MPS (Metal) crashes on Marker

**Symptom:** Marker logs `TableRecEncoderDecoderModel is not compatible with mps backend. Defaulting to cpu instead`.

**Cause:** Marker's bundled Surya models have inconsistent MPS support. Some submodules (table recognition, OCR error detection) don't work on Metal.

**Behaviour:** Marker auto-falls-back to CPU for the affected submodule. The rest still uses MPS where supported. This is a warning, not an error — extraction continues normally.

**Fix:** none required. If you want to silence the warning:

```python
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="surya")
```

## "extract failed: pix to image conversion failed" or similar PIL errors

**Symptom:** Marker fails on a specific PDF with PIL/Pillow errors.

**Cause:** the PDF has malformed embedded images (corrupt JPEG, exotic colour space, etc.).

**Behaviour:** the bulk cron logs `[FAIL] <citekey>` and moves on. The cache is not populated for that PDF.

**Fix:** if this paper is important, try `backend="pymupdf4llm"` — it ignores images entirely. If both fail, the PDF is genuinely broken and needs upstream repair (re-download from publisher, or mark as un-extractable).

## How to inspect what's in the cache

```bash
# Count cached entries by backend
ls /Volumes/SSD/pdf-extract-cache | awk -F_ '{print $2"_"$3}' | sort | uniq -c

# Total disk usage
du -sh /Volumes/SSD/pdf-extract-cache

# Find a specific PDF in cache
sha=$(python3 -c "import hashlib; print(hashlib.sha1(open('paper.pdf','rb').read()).hexdigest()[:16])")
ls /Volumes/SSD/pdf-extract-cache/${sha}_*

# View extracted markdown for a cached PDF
cat /Volumes/SSD/pdf-extract-cache/${sha}_marker_v0.1.0/doc.json | jq -r .markdown | head -50
```

## How to force a re-extraction

```bash
# Single PDF
pdf-extract paper.pdf --backend marker --no-cache

# Or evict the cache entry first, then re-call:
python3 -c "from pdf_extract import cache; from pathlib import Path; cache.evict(Path('paper.pdf'), 'marker', '0.1.0')"
pdf-extract paper.pdf --backend marker
```

## When to bump `EXTRACT_VERSION`

Bump from `0.1.0` → `0.1.1` (or higher) in `src/pdf_extract/dispatch.py` whenever:

- `ExtractedDoc` gets a new field (existing caches won't have it)
- A regex in `_split_references` or `_harvest_tables` changes meaningfully
- A backend's metadata extraction changes
- pymupdf4llm or marker-pdf upgrade alters output materially

A bump is invisible to callers — they keep calling `extract()`. The cache regenerates lazily as papers are re-read. The bulk cron will pick up the bump and re-extract everything on its next pass (which takes weeks; if you need faster invalidation, manually `rm -rf` the cache).
