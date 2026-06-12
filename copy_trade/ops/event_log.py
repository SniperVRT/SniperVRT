"""Persistent event log: structlog processor writes every event to events table."""

from __future__ import annotations

import json
import socket
import sqlite3
from typing import Any

from ..db import connect, utc_now_iso

_HOST = socket.gethostname()


def db_writer_processor(db_path_getter):
    """Create a structlog processor that writes events to SQLite.

    Fails silent on DB unavailable so logging never breaks the worker.
    """
    def _processor(_logger: Any, _method: str, event_dict: dict) -> dict:
        try:
            with connect(db_path_getter()) as c:
                c.execute(
                    "INSERT INTO events (ts, level, logger, event, payload_json, host) "
                    "VALUES (?,?,?,?,?,?)",
                    (utc_now_iso(),
                     event_dict.get("level", "info"),
                     event_dict.get("logger", ""),
                     event_dict.get("event", ""),
                     json.dumps({k: v for k, v in event_dict.items()
                                  if k not in ("level", "logger", "event")},
                                default=str)[:4000],
                     _HOST),
                )
        except Exception:  # noqa: BLE001
            pass
        return event_dict
    return _processor


def tail_events(conn: sqlite3.Connection, n: int = 100,
                level: str | None = None) -> list[dict]:
    sql = "SELECT * FROM events"
    args: tuple = ()
    if level:
        sql += " WHERE level >= ?"
        args = (level,)
    sql += " ORDER BY id DESC LIMIT ?"
    args = args + (n,)
    return [dict(r) for r in conn.execute(sql, args).fetchall()]
