"""Tests for fractional Kelly + risk parity sizing."""

from __future__ import annotations

import pytest
from copy_trade.allocation.sizing import fractional_kelly, risk_parity
from copy_trade.config import CopyTradeSettings
from copy_trade.ranking.score import TraderScore


def _s(uid, comp=0.5):
    return TraderScore(master_uid=uid, snapshot_id=1, sharpe_est=1.0,
                        calmar_est=1.0, composite=comp, rank=1,
                        eligible=True, filter_reason=None)


def _settings():
    return CopyTradeSettings(
        db_path=":memory:", total_capital_usdt=1000.0,
        max_allocation_pct=0.30, min_allocation_pct=0.05,
        kelly_fraction=0.25,
    )


def test_kelly_zero_sigma_skipped():
    s = _settings()
    out = fractional_kelly(
        [_s("a"), _s("b")],
        {"a": [0.01, 0.01, 0.01], "b": [0.02, -0.01, 0.03]},
        s,
    )
    # 'a' has zero variance → skipped
    assert "a" not in out
    assert "b" in out


def test_kelly_negative_mu_skipped():
    s = _settings()
    out = fractional_kelly(
        [_s("a")], {"a": [-0.02, -0.01, -0.03]}, s,
    )
    assert out == {}


def test_risk_parity_equal_vols_equal_weight():
    s = _settings()
    out = risk_parity(
        [_s("a"), _s("b")],
        {"a": [0.01, -0.01, 0.01], "b": [0.01, -0.01, 0.01]},
        s,
    )
    # Equal vols → equal weights
    assert abs(out["a"] - out["b"]) < 1.0


def test_caps_applied():
    s = _settings()
    out = fractional_kelly(
        [_s("dominant")], {"dominant": [0.10, 0.10, 0.10, -0.01]}, s,
    )
    if out:
        # Should be capped at 30%
        assert max(out.values()) <= s.total_capital_usdt * s.max_allocation_pct + 1
