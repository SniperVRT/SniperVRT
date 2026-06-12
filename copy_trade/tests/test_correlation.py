"""Tests for correlation dedup."""

from __future__ import annotations

import pytest
from copy_trade.ranking.correlation import (
    dedup_correlated, pearson_correlation, returns_from_equity_curve,
)


def test_returns_from_equity_curve():
    history = [[1, "100"], [2, "110"], [3, "121"]]
    rets = returns_from_equity_curve(history)
    assert rets == pytest.approx([0.10, 0.10])


def test_returns_from_equity_curve_empty():
    assert returns_from_equity_curve([]) == []
    assert returns_from_equity_curve([[1, "100"]]) == []


def test_pearson_perfect_positive():
    a = [0.01, 0.02, 0.03, 0.04, 0.05]
    b = [0.10, 0.20, 0.30, 0.40, 0.50]
    assert pearson_correlation(a, b) == pytest.approx(1.0, abs=1e-6)


def test_pearson_negative():
    a = [0.01, 0.02, 0.03, 0.04, 0.05]
    b = [0.05, 0.04, 0.03, 0.02, 0.01]
    assert pearson_correlation(a, b) == pytest.approx(-1.0, abs=1e-6)


def test_pearson_too_few_points():
    assert pearson_correlation([1.0, 2.0], [1.0, 2.0]) is None


def test_dedup_drops_highly_correlated():
    class S:
        def __init__(self, uid, composite):
            self.master_uid = uid
            self.composite = composite
    high = S("high", 0.9)
    twin = S("twin", 0.7)
    uncorr = S("uncorr", 0.5)
    returns = {
        "high":  [0.01, 0.02, 0.03, 0.04, 0.05, 0.06],
        "twin":  [0.01, 0.02, 0.03, 0.04, 0.05, 0.06],  # identical
        "uncorr":[0.06, -0.01, 0.03, -0.02, 0.04, 0.01],
    }
    kept = dedup_correlated([high, twin, uncorr], returns, threshold=0.9)
    kept_uids = [k.master_uid for k in kept]
    assert "high" in kept_uids
    assert "twin" not in kept_uids
    assert "uncorr" in kept_uids
