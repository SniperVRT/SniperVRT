"""Tests for allocation/portfolio.py."""

from __future__ import annotations

import pytest
from copy_trade.allocation.portfolio import compute_allocations, _score_weighted_allocation
from copy_trade.config import CopyTradeSettings
from copy_trade.ranking.score import TraderScore


def _score(uid: str, score: float, eligible: bool = True) -> TraderScore:
    return TraderScore(
        master_uid=uid, snapshot_id=1, sharpe_est=1.0, calmar_est=2.0,
        composite=score, rank=1, eligible=eligible, filter_reason=None,
    )


def _settings(**kwargs) -> CopyTradeSettings:
    return CopyTradeSettings(
        db_path=":memory:",
        total_capital_usdt=1000.0,
        max_masters=5,
        min_allocation_usdt=20.0,
        max_allocation_pct=0.30,
        min_allocation_pct=0.05,
        min_track_record_days=0,
        **kwargs,
    )


def test_allocation_caps_at_max_pct():
    s = _settings()
    scores = [_score("a", 1.0), _score("b", 0.01)]  # a should be capped at 30%
    result = _score_weighted_allocation(scores, s)
    for uid, usdt in result.items():
        pct = usdt / s.total_capital_usdt
        assert pct <= s.max_allocation_pct + 0.001


def test_allocation_floor_at_min_pct():
    s = _settings()
    scores = [_score("a", 1.0), _score("b", 0.001)]
    result = _score_weighted_allocation(scores, s)
    for uid, usdt in result.items():
        pct = usdt / s.total_capital_usdt
        assert pct >= s.min_allocation_pct - 0.001


def test_compute_subscribe_decision():
    s = _settings()
    scores = [_score("new_trader", 0.8)]
    decisions = compute_allocations(scores, current_subs=[], settings=s)
    assert len(decisions) == 1
    assert decisions[0].action == "subscribe"
    assert decisions[0].target_usdt >= s.min_allocation_usdt


def test_compute_unsubscribe_ineligible():
    s = _settings()
    scores = [_score("old", 0.1, eligible=False)]
    current = [{"master_uid": "old", "allocated_usdt": 100.0}]
    decisions = compute_allocations(scores, current_subs=current, settings=s)
    assert any(d.action == "unsubscribe" for d in decisions)


def test_compute_keep_within_threshold():
    s = _settings()
    scores = [_score("stable", 0.7)]
    # Current allocation is already close to target
    current = [{"master_uid": "stable", "allocated_usdt": 300.0}]
    # With 1 eligible master at 30% cap → target = min(0.30, ...) * 1000 = 300
    decisions = compute_allocations(scores, current_subs=current, settings=s)
    assert any(d.action in ("keep", "rebalance") for d in decisions)


def test_no_eligible_triggers_unsubscribe_all():
    s = _settings()
    scores = [_score("a", 0.5, eligible=False)]
    current = [{"master_uid": "a", "allocated_usdt": 200.0}]
    decisions = compute_allocations(scores, current_subs=current, settings=s)
    assert all(d.action == "unsubscribe" for d in decisions)
