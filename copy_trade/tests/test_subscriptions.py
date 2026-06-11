"""Tests for execution/subscriptions.py."""

from __future__ import annotations

import pytest
from copy_trade.allocation.portfolio import AllocationDecision
from copy_trade.execution.subscriptions import execute_decisions, active_subscriptions
from copy_trade.traders.store import upsert_master
from copy_trade.platforms.base import MasterStats


def _master(uid: str) -> MasterStats:
    return MasterStats(
        uid=uid, platform="bitget", nickname="T",
        roi_7d=0.02, roi_30d=0.08, roi_all=0.25,
        mdd=-0.10, win_rate=0.55, total_trades=80,
        followers=10, aum_usdt=2000.0, avg_holding_h=2.0, sharpe=None,
    )


def _decision(uid: str, action: str, allocated=100.0, current=0.0) -> AllocationDecision:
    return AllocationDecision(
        master_uid=uid, action=action,
        current_usdt=current, target_usdt=allocated,
        score=0.5, reason="test",
    )


def test_subscribe_dry_run_no_api_call(conn, settings):
    upsert_master(conn, _master("abc"))
    decisions = [_decision("abc", "subscribe", 100.0)]
    counts = execute_decisions(conn, decisions, connector=None,
                               settings=settings, dry_run=True)
    assert counts["subscribe"] == 1
    subs = active_subscriptions(conn)
    assert len(subs) == 1
    assert subs[0]["master_uid"] == "abc"
    # No API call made (dry_run=True), but row is persisted
    assert subs[0]["external_sub_id"] == ""


def test_subscribe_idempotent(conn, settings):
    upsert_master(conn, _master("abc"))
    d = _decision("abc", "subscribe", 100.0)
    execute_decisions(conn, [d], connector=None, settings=settings, dry_run=True)
    # Second call with same master in active subs → keep not subscribe
    counts = execute_decisions(conn, [d], connector=None, settings=settings, dry_run=True)
    assert counts["subscribe"] == 0
    assert counts["keep"] == 1


def test_unsubscribe_marks_inactive(conn, settings):
    upsert_master(conn, _master("xyz"))
    sub_d = _decision("xyz", "subscribe", 100.0)
    execute_decisions(conn, [sub_d], connector=None, settings=settings, dry_run=True)
    # Now unsubscribe
    unsub_d = _decision("xyz", "unsubscribe", 0.0, 100.0)
    execute_decisions(conn, [unsub_d], connector=None, settings=settings, dry_run=True)
    subs = active_subscriptions(conn)
    assert not any(s["master_uid"] == "xyz" for s in subs)


def test_safety_gate_blocks_when_drawdown_exceeded(conn, settings):
    from copy_trade.db import utc_now_iso
    settings.max_total_drawdown_pct = 0.10
    # Plant a portfolio snapshot with 15% drawdown
    conn.execute(
        "INSERT INTO portfolio_snapshots "
        "(captured_at, total_capital, deployed_usdt, realized_pnl, "
        "unrealized_pnl, peak_equity, drawdown_pct, active_masters) "
        "VALUES (?,1000,500,-50,-100,1000,0.15,2)",
        (utc_now_iso(),),
    )
    upsert_master(conn, _master("m1"))
    decisions = [_decision("m1", "subscribe", 100.0)]
    counts = execute_decisions(conn, decisions, settings=settings, dry_run=True)
    assert counts["subscribe"] == 0  # all blocked by safety gate
