"""Tests for the entry_equity_usdt subscribe-time stamp."""

from __future__ import annotations

import json

from copy_trade.execution.subscriptions import vault_equity_from_db
from copy_trade.db import utc_now_iso


def _seed(conn, uid="v1", history_values=None):
    history = history_values or [[1, "100"], [2, "120"], [3, "150"]]
    conn.execute(
        "INSERT INTO masters (uid, platform, first_seen_at, last_seen_at, active) "
        "VALUES (?, 'hyperliquid', ?, ?, 1)",
        (uid, utc_now_iso(), utc_now_iso()),
    )
    conn.execute(
        "INSERT INTO trader_snapshots (master_uid, captured_at, raw_json) "
        "VALUES (?, ?, ?)",
        (uid, utc_now_iso(),
         json.dumps({
             "portfolio": [["allTime", {"accountValueHistory": history}]],
         })),
    )


def test_vault_equity_pulls_latest_value(conn):
    _seed(conn, "v1", history_values=[[1, "100"], [2, "200"], [3, "300"]])
    assert vault_equity_from_db(conn, "v1") == 300.0


def test_vault_equity_returns_none_without_history(conn):
    conn.execute(
        "INSERT INTO masters (uid, platform, first_seen_at, last_seen_at, active) "
        "VALUES ('v2', 'hyperliquid', ?, ?, 1)",
        (utc_now_iso(), utc_now_iso()),
    )
    assert vault_equity_from_db(conn, "v2") is None


def test_vault_equity_handles_malformed_json(conn):
    conn.execute(
        "INSERT INTO masters (uid, platform, first_seen_at, last_seen_at, active) "
        "VALUES ('v3', 'hyperliquid', ?, ?, 1)",
        (utc_now_iso(), utc_now_iso()),
    )
    conn.execute(
        "INSERT INTO trader_snapshots (master_uid, captured_at, raw_json) "
        "VALUES ('v3', ?, 'not json')",
        (utc_now_iso(),),
    )
    assert vault_equity_from_db(conn, "v3") is None
