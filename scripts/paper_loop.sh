#!/usr/bin/env bash
# Standalone paper-trading loop (without the API). Useful for headless runs.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$(pwd):${PYTHONPATH:-}"
exec python3 -c "
import asyncio, time
from backend.app.core.db import init_db
from backend.app.data import ensure_dataset
from backend.app.paper import get_runtime
init_db(); ensure_dataset()
runtime = get_runtime()
async def main():
    runtime.start()
    while True:
        await asyncio.sleep(5)
asyncio.run(main())
"
