"""Tests for unified portfolio aggregator + safety cascade."""

from __future__ import annotations

import os
import pytest

from unified.aggregator import aggregate
from unified.db import connect as unified_connect, init_db as unified_init
from unified.safety import cross_system_safety_check


def test_aggregate_with_no_subsystem_dbs(tmp_path, monkeypatch):
    udb = tmp_path / "unified.db"
    monkeypatch.setenv("UNIFIED_DB_PATH", str(udb))
    monkeypatch.setenv("KALSHI_DB_PATH", str(tmp_path / "kalshi_missing.db"))
    monkeypatch.setenv("COPYTRADE_DB_PATH", str(tmp_path / "copytrade_missing.db"))
    unified_init(udb)
    with unified_connect(udb) as c:
        out = aggregate(c)
    assert out["total_equity"] == 0
    assert out["drawdown_pct"] == 0


def test_safety_cascade_no_snapshot(tmp_path, monkeypatch):
    udb = tmp_path / "unified.db"
    monkeypatch.setenv("UNIFIED_DB_PATH", str(udb))
    unified_init(udb)
    with unified_connect(udb) as c:
        out = cross_system_safety_check(c)
    assert not out["engaged"]
