"""Tests for data quality gates + circuit breakers + macro calendar."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest
from copy_trade.connectors_util import (
    CircuitBreaker, validate_hl_vault, validate_kalshi_market,
)
from copy_trade.external.macro_calendar import (
    in_caution_window, EVENTS, CAUTION_WINDOW,
)


def test_validate_hl_negative_tvl():
    assert validate_hl_vault({"vaultAddress": "0x", "tvl": -100}) == "negative_tvl"


def test_validate_hl_future_create_time():
    future_ms = int(time.time() * 1000) + 10 * 60 * 1000
    assert validate_hl_vault({
        "vaultAddress": "0x", "tvl": 100, "createTimeMillis": future_ms,
    }) == "future_create_time"


def test_validate_hl_ok():
    assert validate_hl_vault({"vaultAddress": "0x", "tvl": 1000}) is None


def test_validate_kalshi_negative_volume():
    assert validate_kalshi_market({"ticker": "X", "volume": -5}) == "negative_volume"


def test_circuit_breaker_opens_after_threshold():
    cb = CircuitBreaker(name="x", failure_threshold=3, cool_down_s=10.0)
    for _ in range(3):
        cb.record_failure()
    assert cb.is_open()


def test_circuit_breaker_recovers():
    cb = CircuitBreaker(name="x", failure_threshold=3, cool_down_s=0.01)
    for _ in range(3):
        cb.record_failure()
    time.sleep(0.02)
    assert not cb.is_open()


def test_macro_calendar_returns_none_outside_window():
    far_future = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert in_caution_window(far_future) is None


def test_macro_calendar_format():
    # Just confirm calendar list is non-empty and parseable.
    assert len(EVENTS) > 0
    for iso, name in EVENTS:
        datetime.fromisoformat(iso)
        assert name
