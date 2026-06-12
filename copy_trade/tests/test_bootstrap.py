"""Tests for bootstrap + sample size + sweep."""

from __future__ import annotations

import pytest
from copy_trade.backtest.bootstrap import bootstrap_ci, sharpe
from copy_trade.backtest.sweep import parse_range, robust_region
from copy_trade.validation.sample_size import (
    confidence_tag, require_min_samples, InsufficientSamplesError,
)


def test_bootstrap_too_few():
    out = bootstrap_ci([1.0, 2.0])
    assert out["lo"] is None


def test_bootstrap_stable_estimate():
    vals = [0.05] * 20
    out = bootstrap_ci(vals, n_resamples=200)
    assert out["lo"] == pytest.approx(0.05, abs=1e-9)
    assert out["hi"] == pytest.approx(0.05, abs=1e-9)


def test_sharpe_positive():
    rets = [0.01, 0.02, 0.005, 0.015, 0.01, 0.02]
    assert sharpe(rets) > 0


def test_parse_range_inclusive():
    vals = parse_range("0.2,0.5,0.1")
    assert vals[0] == 0.2
    assert vals[-1] == pytest.approx(0.5)


def test_robust_region_finds_contiguous():
    results = [
        {"x": 0.1, "sharpe": -0.5},
        {"x": 0.2, "sharpe": 0.2},
        {"x": 0.3, "sharpe": 0.5},
        {"x": 0.4, "sharpe": 0.4},
        {"x": 0.5, "sharpe": -0.1},
    ]
    region = robust_region(results)
    assert region == [0.2, 0.3, 0.4]


def test_sample_size_raises():
    with pytest.raises(InsufficientSamplesError):
        require_min_samples(5, 20, "sharpe")


def test_confidence_tag():
    assert confidence_tag(10) == "insufficient"
    assert confidence_tag(30) == "low_confidence"
    assert confidence_tag(100) == "ok"
