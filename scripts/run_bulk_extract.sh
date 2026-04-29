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

exec "$UV" run python scripts/bulk_extract.py \
    --max-runtime "${PDF_EXTRACT_MAX_RUNTIME:-21600}" \
    --max-papers "${PDF_EXTRACT_MAX_PAPERS:-2000}" \
    --per-paper-timeout 120 \
    >> "$LOG" 2>&1
