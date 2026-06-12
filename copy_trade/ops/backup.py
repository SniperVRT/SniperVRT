"""SQLite WAL checkpoint + atomic .backup snapshots."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path


def checkpoint(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")


def backup(src_path: Path, dest_dir: Path,
           prefix: str = "copy_trade", keep_days: int = 14) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    dest = dest_dir / f"{prefix}_{ts}.db"
    with sqlite3.connect(src_path) as src, sqlite3.connect(dest) as dst:
        src.backup(dst)
    # Cleanup
    cutoff = time.time() - (keep_days * 86400)
    for f in dest_dir.glob(f"{prefix}_*.db"):
        if f.stat().st_mtime < cutoff:
            try:
                f.unlink()
            except OSError:
                pass
    return dest
