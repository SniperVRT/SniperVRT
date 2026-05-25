import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Use a dedicated test DB. Set BEFORE any backend.* imports so Settings picks it up.
TEST_DB = ROOT / "runtime_state" / "test_platform.db"
TEST_DB.parent.mkdir(parents=True, exist_ok=True)
if TEST_DB.exists():
    TEST_DB.unlink()
os.environ["SNIPER_DB_URL"] = f"sqlite:///{TEST_DB.as_posix()}"

from backend.app.core.db import init_db  # noqa: E402

init_db()
