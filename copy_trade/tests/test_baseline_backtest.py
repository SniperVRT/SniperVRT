"""Tests for statistical baseline backtests."""

from __future__ import annotations

import pytest
from copy_trade.validation.baseline import (
    equal_weight_top_n, random_picks, _summarize,
)


def test_summarize_basic():
    s = _summarize([100, 110, 105, 115], 100, 3)
    assert s["final_equity"] == 115
    assert s["total_return"] == pytest.approx(0.15)


def test_summarize_no_ticks():
    s = _summarize([1000], 1000, 0)
    assert s["final_equity"] == 1000
    assert s["total_return"] == 0


def test_baseline_no_data(conn):
    res = equal_weight_top_n(conn)
    assert res["n_ticks"] == 0
    assert res["final_equity"] == 1000.0


def test_random_picks_no_data(conn):
    res = random_picks(conn)
    assert res["n_ticks"] == 0
