#!/usr/bin/env bash
# Start the BTC AI trading platform (backend + UI on http://localhost:8000).
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$(pwd):${PYTHONPATH:-}"
exec python3 -m uvicorn backend.app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --reload
