"""Tests for persistent event log + backup."""

from __future__ import annotations

from pathlib import Path

import pytest
from copy_trade.ops.backup import backup, checkpoint
from copy_trade.ops.event_log import db_writer_processor, tail_events
from copy_trade.db import connect


def test_event_log_writer_persists(tmp_path):
    db_path = tmp_path / "x.db"
    # init schema
    from copy_trade.db import init_db
    init_db(db_path)
    proc = db_writer_processor(lambda: db_path)
    proc(None, "info", {"level": "info", "event": "test", "logger": "x", "foo": 1})
    with connect(db_path) as c:
        evs = tail_events(c, n=10)
    assert any(e["event"] == "test" for e in evs)


def test_backup_creates_file(tmp_path):
    src = tmp_path / "src.db"
    # Create a minimal SQLite DB
    import sqlite3
    with sqlite3.connect(src) as c:
        c.execute("CREATE TABLE t (x INTEGER)")
        c.execute("INSERT INTO t VALUES (1)")
    out = backup(src, tmp_path / "backups", prefix="test")
    assert out.exists()
    assert out.stat().st_size > 0


def test_checkpoint_safe(tmp_path):
    from copy_trade.db import init_db
    init_db(tmp_path / "x.db")
    with connect(tmp_path / "x.db") as c:
        checkpoint(c)  # should not raise
