#!/usr/bin/env bash
# Wrapper for launchd: ensures the right venv + cwd before running bulk_extract.py
set -euo pipefail

PDF_EXTRACT_DIR="/Users/florianburnat/Task-Management/packages/pdf-extract"
LOG_DIR="$PDF_EXTRACT_DIR/log"
mkdir -p "$LOG_DIR"

LOG="$LOG_DIR/bulk-extract-$(date +%Y%m%d-%H%M%S).log"

cd "$PDF_EXTRACT_DIR"

# 6h budget by default; can be overridden via env. Per-paper 120s timeout.
exec /opt/homebrew/bin/uv run python scripts/bulk_extract.py \
    --max-runtime "${PDF_EXTRACT_MAX_RUNTIME:-21600}" \
    --max-papers "${PDF_EXTRACT_MAX_PAPERS:-2000}" \
    --per-paper-timeout 120 \
    >> "$LOG" 2>&1
