"""Tests for portfolio-level risk."""

from __future__ import annotations

import json
import time

import pytest
from copy_trade.risk.portfolio_risk import (
    liquidity_summary, same_leader_concentration, snapshot,
)
from copy_trade.traders.store import upsert_master
from copy_trade.platforms.base import MasterStats
from copy_trade.db import utc_now_iso


def _seed(conn, uid, leader="0xleader", lockup_offset_ms=0, allocated=100.0):
    upsert_master(conn, MasterStats(
        uid=uid, platform="hyperliquid", nickname="",
        roi_7d=None, roi_30d=None, roi_all=None, mdd=None,
        win_rate=None, total_trades=None, followers=None,
        aum_usdt=None, avg_holding_h=None, sharpe=None,
    ))
    conn.execute(
        "INSERT INTO trader_snapshots (master_uid, captured_at, raw_json) "
        "VALUES (?, ?, ?)",
        (uid, utc_now_iso(), json.dumps({"leader": leader, "portfolio": []})),
    )
    lockup = int(time.time() * 1000) + lockup_offset_ms if lockup_offset_ms else 0
    conn.execute(
        "INSERT INTO subscriptions (master_uid, platform, mode, allocated_usdt, "
        "subscribed_at, status, external_sub_id, lockup_until_ms) "
        "VALUES (?, 'hyperliquid', 'live', ?, ?, 'active', '', ?)",
        (uid, allocated, utc_now_iso(), lockup),
    )


def test_same_leader_concentration(conn):
    _seed(conn, "a", leader="0xLEAD")
    _seed(conn, "b", leader="0xLEAD")
    _seed(conn, "c", leader="0xother")
    out = same_leader_concentration(conn)
    assert out["0xLEAD"] == pytest.approx(200.0)
    assert out["0xother"] == pytest.approx(100.0)


def test_liquidity_summary_locked_vs_unlocked(conn):
    _seed(conn, "locked", lockup_offset_ms=86400 * 1000)
    _seed(conn, "free", lockup_offset_ms=0)
    out = liquidity_summary(conn)
    assert out["locked_usdt"] == pytest.approx(100.0)
    assert out["unlocked_usdt"] == pytest.approx(100.0)


def test_snapshot_engages_concentration_lock(conn, settings):
    _seed(conn, "a", leader="0xLEAD", allocated=400.0)
    _seed(conn, "b", leader="0xLEAD", allocated=100.0)
    snapshot(conn, settings)
    row = conn.execute(
        "SELECT * FROM safety_events WHERE event_type='concentration' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None
