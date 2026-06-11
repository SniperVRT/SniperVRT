"""Tests for backtest engine."""

from __future__ import annotations

import pytest
from copy_trade.backtest.engine import (
    _series_sharpe, _tick_timestamps, run_backtest,
)
from copy_trade.platforms.base import MasterStats
from copy_trade.traders.store import insert_snapshot, upsert_master
from copy_trade.db import utc_now_iso


def test_series_sharpe_too_few_points():
    assert _series_sharpe([100.0, 110.0]) is None


def test_series_sharpe_steady_growth():
    series = [100.0, 101.0, 102.01, 103.03, 104.06]
    s = _series_sharpe(series)
    # Constant 1% returns → variance 0 → None
    assert s is None or s > 10


def test_series_sharpe_mixed():
    series = [100, 105, 102, 108, 104, 110]
    s = _series_sharpe(series)
    assert s is not None


def test_tick_timestamps_filters_by_window(conn):
    from datetime import datetime, timezone, timedelta
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # 10 snapshots one hour apart
    for i in range(10):
        ts = (base + timedelta(hours=i)).isoformat()
        conn.execute(
            "INSERT INTO masters (uid, platform, first_seen_at, last_seen_at) "
            "VALUES ('t', 'hl', ?, ?)" if i == 0 else
            "UPDATE masters SET last_seen_at=? WHERE uid='t' OR ?=''",
            (ts, ts),
        )
        conn.execute(
            "INSERT INTO trader_snapshots (master_uid, captured_at, aum_usdt) "
            "VALUES ('t', ?, 100000)",
            (ts,),
        )
    ticks = _tick_timestamps(conn, rebalance_hours=3)
    # Expect ~4 ticks: 0h, 3h, 6h, 9h
    assert 3 <= len(ticks) <= 4


def test_run_backtest_with_no_data(conn, settings):
    res = run_backtest(conn, initial_capital=1000.0, settings=settings)
    assert res.n_ticks == 0
    assert res.final_equity == pytest.approx(1000.0)
