"""Tests for platforms/hyperliquid.py — parsing + MDD computation."""

from __future__ import annotations

import pytest

from copy_trade.platforms.hyperliquid import (
    _compute_mdd_from_history,
    _parse_vault_summary,
    _merge_details,
    _roi_over_history,
)


def test_compute_mdd_basic():
    history = [[1, "100"], [2, "120"], [3, "90"], [4, "110"]]
    # Peak hit at 120, trough at 90 → -25%
    mdd = _compute_mdd_from_history(history)
    assert mdd == pytest.approx(-0.25)


def test_compute_mdd_monotonic_up():
    history = [[1, "100"], [2, "110"], [3, "120"]]
    mdd = _compute_mdd_from_history(history)
    assert mdd == pytest.approx(0.0)


def test_compute_mdd_empty():
    assert _compute_mdd_from_history([]) is None
    assert _compute_mdd_from_history([[1, "100"]]) is None


def test_compute_mdd_malformed():
    assert _compute_mdd_from_history([["x", "y"], [1, "abc"]]) is None


def test_parse_vault_summary_minimal():
    raw = {
        "vaultAddress": "0xabc",
        "name": "BestVault",
        "leader": "0xleader",
        "tvl": "12345.67",
        "isClosed": False,
    }
    m = _parse_vault_summary(raw)
    assert m.uid == "0xabc"
    assert m.platform == "hyperliquid"
    assert m.nickname == "BestVault"
    assert m.aum_usdt == pytest.approx(12345.67)
    # APR/MDD aren't in summaries — must be None
    assert m.roi_all is None
    assert m.mdd is None


def test_merge_details_computes_mdd_from_history():
    summary = _parse_vault_summary({
        "vaultAddress": "0xabc", "name": "X", "tvl": "1000",
    })
    details = {
        "apr": 0.50,
        "followers": [{"user": "0x1"}, {"user": "0x2"}, {"user": "0x3"}],
        "portfolio": [
            ["allTime", {
                "accountValueHistory": [[1, "100"], [2, "150"], [3, "75"]],
            }],
            ["month", {
                "accountValueHistory": [[1, "100"], [2, "105"]],
            }],
            ["week", {
                "accountValueHistory": [[1, "100"], [2, "103"]],
            }],
        ],
    }
    enriched = _merge_details(summary, details)
    assert enriched.roi_all == pytest.approx(0.50)
    assert enriched.followers == 3
    # MDD from allTime: peak 150 → trough 75 → -50%
    assert enriched.mdd == pytest.approx(-0.50)
    assert enriched.roi_30d == pytest.approx(0.05)
    assert enriched.roi_7d == pytest.approx(0.03)


def test_roi_over_history():
    assert _roi_over_history([[1, "100"], [2, "110"]]) == pytest.approx(0.10)
    assert _roi_over_history([]) is None
    assert _roi_over_history([[1, "0"], [2, "100"]]) is None
