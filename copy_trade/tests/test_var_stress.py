"""Tests for VaR/CVaR and stress scenarios."""

from __future__ import annotations

import pytest
from copy_trade.risk.var import var_cvar_95, daily_returns
from copy_trade.risk.stress import stress_test


def test_var_too_few_samples():
    assert var_cvar_95([0.01] * 5, 1000.0) == (None, None)


def test_var_cvar_basic():
    rets = [-0.05, -0.03, -0.02, -0.01, 0.0, 0.01, 0.01, 0.02, 0.02, 0.03,
            0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01,
            -0.04, -0.06]
    var, cvar = var_cvar_95(rets, 1000.0)
    assert var is not None and var > 0
    assert cvar is not None and cvar >= var


def test_daily_returns_skips_zero_baseline():
    assert daily_returns([0, 100, 110]) == pytest.approx([0.10])


def test_stress_test_no_subs(conn, settings):
    assert stress_test(conn, settings) == []
