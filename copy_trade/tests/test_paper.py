"""Tests for paper trading executor."""

from __future__ import annotations

import json
import pytest

from copy_trade.allocation.portfolio import AllocationDecision
from copy_trade.paper.executor import (
    active_paper_subscriptions, execute_paper_decisions,
)
from copy_trade.traders.store import upsert_master
from copy_trade.platforms.base import MasterStats


def _master(uid: str) -> MasterStats:
    return MasterStats(
        uid=uid, platform="hyperliquid", nickname="T",
        roi_7d=0.02, roi_30d=0.08, roi_all=0.25, mdd=-0.10,
        win_rate=0.55, total_trades=80, followers=10,
        aum_usdt=2000.0, avg_holding_h=2.0, sharpe=None,
    )


def _d(uid: str, action: str, allocated=100.0, current=0.0) -> AllocationDecision:
    return AllocationDecision(
        master_uid=uid, action=action,
        current_usdt=current, target_usdt=allocated,
        score=0.5, reason="test",
    )


def test_paper_subscribe_persists_with_mode(conn, settings):
    upsert_master(conn, _master("p1"))
    execute_paper_decisions(conn, [_d("p1", "subscribe", 100.0)], settings)
    subs = active_paper_subscriptions(conn)
    assert len(subs) == 1
    assert subs[0]["mode"] == "paper"
    assert subs[0]["allocated_usdt"] == pytest.approx(100.0)


def test_paper_does_not_appear_in_live_subscriptions(conn, settings):
    """Critical: paper mode must NEVER show up in the live executor's view."""
    from copy_trade.execution.subscriptions import active_subscriptions
    upsert_master(conn, _master("p1"))
    execute_paper_decisions(conn, [_d("p1", "subscribe", 100.0)], settings)
    live_subs = active_subscriptions(conn)
    assert all(s["mode"] != "paper" for s in live_subs)


def test_paper_unsubscribe_respects_lockup(conn, settings):
    import time
    upsert_master(conn, _master("p1"))
    execute_paper_decisions(conn, [_d("p1", "subscribe", 100.0)], settings)
    # Subscribe sets a 24h lockup → immediate unsubscribe blocked
    counts = execute_paper_decisions(
        conn, [_d("p1", "unsubscribe", 0.0, 100.0)], settings,
    )
    assert counts["lockup_blocked"] == 1
    assert counts["unsubscribe"] == 0
