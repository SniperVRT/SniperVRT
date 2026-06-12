"""Unified portfolio SQLite."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_path() -> Path:
    return Path(os.environ.get("UNIFIED_DB_PATH", "./data/unified.db"))


def kalshi_db_path() -> Path:
    return Path(os.environ.get("KALSHI_DB_PATH", "./kalshi/data/kalshi.db"))


def copytrade_db_path() -> Path:
    return Path(os.environ.get("COPYTRADE_DB_PATH", "./copy_trade/data/copy_trade.db"))


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p, isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON;")
    c.execute("PRAGMA journal_mode = WAL;")
    return c


def init_db(path: Path | None = None) -> None:
    sql = SCHEMA_PATH.read_text()
    with connect(path) as c:
        c.executescript(sql)
