"""Tests for traders/store.py."""

from __future__ import annotations

import pytest
from copy_trade.platforms.base import MasterStats
from copy_trade.traders.store import (
    upsert_master, insert_snapshot, mark_inactive,
    active_masters, track_record_days, roi_series,
)


def _m(uid="t1") -> MasterStats:
    return MasterStats(
        uid=uid, platform="bitget", nickname="Trader",
        roi_7d=0.02, roi_30d=0.08, roi_all=0.30,
        mdd=-0.12, win_rate=0.60, total_trades=100,
        followers=20, aum_usdt=5000.0, avg_holding_h=1.5, sharpe=None,
    )


def test_upsert_master_idempotent(conn):
    m = _m()
    upsert_master(conn, m)
    upsert_master(conn, m)  # should not raise
    rows = conn.execute("SELECT COUNT(*) FROM masters WHERE uid='t1'").fetchone()[0]
    assert rows == 1


def test_insert_snapshot_returns_id(conn):
    m = _m()
    upsert_master(conn, m)
    snap_id = insert_snapshot(conn, m)
    assert snap_id > 0


def test_mark_inactive_removes_missing(conn):
    for uid in ["a", "b", "c"]:
        upsert_master(conn, _m(uid))
    # Only "a" and "b" are still active
    deactivated = mark_inactive(conn, "bitget", {"a", "b"})
    assert deactivated == 1
    actives = {r["uid"] for r in active_masters(conn, "bitget")}
    assert "c" not in actives
    assert "a" in actives


def test_track_record_days_zero_with_one_snapshot(conn):
    m = _m()
    upsert_master(conn, m)
    insert_snapshot(conn, m)
    # Only one snapshot → no time difference
    assert track_record_days(conn, "t1") == 0


def test_roi_series_returns_values(conn):
    m = _m()
    upsert_master(conn, m)
    for _ in range(5):
        insert_snapshot(conn, m)
    series = roi_series(conn, "t1")
    assert len(series) == 5
    assert all(v == pytest.approx(0.08) for v in series)
