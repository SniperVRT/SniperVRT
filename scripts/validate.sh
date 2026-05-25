#!/usr/bin/env bash
# Run the governance validation pipeline and print the council decision.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$(pwd):${PYTHONPATH:-}"
python3 -c "
from backend.app.core.db import init_db
from backend.app.data import ensure_dataset
from backend.app.governance import generate_council_decision
import json
init_db()
ensure_dataset()
out = generate_council_decision()
print(json.dumps(out, indent=2, default=str))
"
