#!/usr/bin/env bash
# Wrapper for launchd: ensures the right venv + cwd before running bulk_extract.py
#
# Set these env vars before invoking (or via launchd EnvironmentVariables):
#   PAPERPILE_JSON         — path to Paperpile JSON export
#   PAPERPILE_PDF_ROOT     — path to local PDF mirror
#   RESEARCH_PROJECTS_ROOT — (optional) path to research projects tree for bib-priority
#   PDF_EXTRACT_CACHE_DIR  — (optional) override cache root
#   PDF_EXTRACT_MAX_RUNTIME — (optional) wall-clock budget in seconds (default 21600 = 6h)
#   PDF_EXTRACT_MAX_PAPERS  — (optional) hard cap per run (default 2000)
#   PDF_EXTRACT_MAX_RSS_MB  — (optional) own-RSS cap in MB (default 4096; 0 disables);
#                             breach blocklists the paper and ends the run
#   PDF_EXTRACT_MIN_FREE_PERCENT — (optional) stop early if system-wide free
#                             memory %% (memory_pressure -Q) drops below this
#                             between papers (default 15; 0 disables)
set -euo pipefail

# Resolve PDF_EXTRACT_DIR from this script's location, so the wrapper works
# regardless of where the repo is checked out.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PDF_EXTRACT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

LOG_DIR="$PDF_EXTRACT_DIR/log"
mkdir -p "$LOG_DIR"

LOG="$LOG_DIR/bulk-extract-$(date +%Y%m%d-%H%M%S).log"

cd "$PDF_EXTRACT_DIR"

# Prefer the system uv if present (Homebrew on macOS, ~/.local/bin on Linux).
UV="$(command -v uv || echo /opt/homebrew/bin/uv)"

# Hard wall-clock kill via coreutils `timeout` — outer guard in case
# python-side --max-runtime is bypassed by Marker subprocesses or wedges.
# Budget = python timer + 600s grace, so the python timer is preferred path.
TIMEOUT_BIN="$(command -v timeout || command -v gtimeout)"
PY_RUNTIME="${PDF_EXTRACT_MAX_RUNTIME:-21600}"
HARD_RUNTIME=$((PY_RUNTIME + 600))
export PYTHONUNBUFFERED=1

exec "$TIMEOUT_BIN" --kill-after=60 "$HARD_RUNTIME" "$UV" run python scripts/bulk_extract.py \
    --max-runtime "$PY_RUNTIME" \
    --max-papers "${PDF_EXTRACT_MAX_PAPERS:-2000}" \
    --per-paper-timeout 600 \
    >> "$LOG" 2>&1
