#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$(pwd):${PYTHONPATH:-}"
python3 -c "
from backend.app.core.db import init_db
from backend.app.data import ensure_dataset
from backend.app.learning import run_tournament
import json
init_db(); ensure_dataset()
print(json.dumps(run_tournament(), indent=2, default=str))
"
