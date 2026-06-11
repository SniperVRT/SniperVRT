"""Tests for ranking/score.py."""

from __future__ import annotations

import pytest
from copy_trade.ranking.score import (
    _calmar, _sharpe_from_series, _estimate_profit_factor, score_traders,
)
from copy_trade.traders.store import upsert_master, insert_snapshot
from copy_trade.platforms.base import MasterStats


def _make_master(uid="t1", roi_30d=0.10, roi_all=0.25, mdd=-0.15,
                 win_rate=0.55, followers=50, total_trades=200) -> MasterStats:
    return MasterStats(
        uid=uid, platform="bitget", nickname=f"trader_{uid}",
        roi_7d=roi_30d / 4, roi_30d=roi_30d, roi_all=roi_all,
        mdd=mdd, win_rate=win_rate, total_trades=total_trades,
        followers=followers, aum_usdt=10000.0, avg_holding_h=2.0, sharpe=None,
    )


def test_sharpe_from_series_consistent_returns():
    # All returns identical → variance=0 → Sharpe undefined → None
    returns = [0.05] * 12
    s = _sharpe_from_series(returns)
    assert s is None


def test_sharpe_from_series_mixed():
    returns = [0.10, -0.05, 0.08, -0.02, 0.12, 0.03]
    s = _sharpe_from_series(returns)
    assert s is not None
    assert s > 0  # positive mean, moderate std → positive Sharpe


def test_sharpe_from_series_too_short():
    assert _sharpe_from_series([0.1, 0.2]) is None


def test_calmar_normal():
    c = _calmar(0.30, -0.15)
    assert abs(c - 2.0) < 0.01


def test_calmar_zero_mdd():
    c = _calmar(0.50, 0.0)
    assert c is not None
    assert c <= 10.0


def test_calmar_none_inputs():
    assert _calmar(None, -0.1) is None
    assert _calmar(0.3, None) is None


def test_profit_factor_high_win():
    pf = _estimate_profit_factor(0.70, 0.30)
    assert pf > 1.0


def test_profit_factor_zero_win():
    assert _estimate_profit_factor(0.0, 0.0) == 0.0


def test_score_traders_ranks_by_composite(conn, settings):
    # High Sharpe master should outrank low Sharpe master
    m1 = _make_master("high", roi_30d=0.15, roi_all=0.40, mdd=-0.10, win_rate=0.65)
    m2 = _make_master("low", roi_30d=0.03, roi_all=0.05, mdd=-0.30, win_rate=0.45)
    for m in [m1, m2]:
        upsert_master(conn, m)
    snap_id1 = insert_snapshot(conn, m1)
    snap_id2 = insert_snapshot(conn, m2)

    snaps = [
        {"master_uid": "high", "id": snap_id1, "roi_7d": m1.roi_7d,
         "roi_30d": m1.roi_30d, "roi_all": m1.roi_all, "mdd": m1.mdd,
         "win_rate": m1.win_rate, "total_trades": m1.total_trades,
         "followers": m1.followers, "sharpe": None},
        {"master_uid": "low", "id": snap_id2, "roi_7d": m2.roi_7d,
         "roi_30d": m2.roi_30d, "roi_all": m2.roi_all, "mdd": m2.mdd,
         "win_rate": m2.win_rate, "total_trades": m2.total_trades,
         "followers": m2.followers, "sharpe": None},
    ]
    scores = score_traders(conn, snaps, settings)
    assert len(scores) == 2
    assert scores[0].master_uid == "high"
    assert scores[0].composite > scores[1].composite


def test_score_traders_filters_high_mdd(conn, settings):
    settings.max_mdd_pct = 0.20
    m = _make_master("bad", mdd=-0.50)  # 50% MDD > 20% limit
    upsert_master(conn, m)
    snap_id = insert_snapshot(conn, m)
    snaps = [{"master_uid": "bad", "id": snap_id, "roi_7d": m.roi_7d,
              "roi_30d": m.roi_30d, "roi_all": m.roi_all, "mdd": m.mdd,
              "win_rate": m.win_rate, "total_trades": m.total_trades,
              "followers": m.followers, "sharpe": None}]
    scores = score_traders(conn, snaps, settings)
    assert len(scores) == 1
    assert not scores[0].eligible
    assert "mdd" in (scores[0].filter_reason or "")
