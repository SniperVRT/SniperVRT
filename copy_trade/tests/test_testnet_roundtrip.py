"""Tests for testnet roundtrip + lookahead audit + model versions."""

from __future__ import annotations

import pytest
from copy_trade.validation.testnet_roundtrip import run_roundtrip
from copy_trade.validation.lookahead_audit import audit
from copy_trade.validation.model_versions import (
    current_version_string, record_version, list_versions,
)


def test_testnet_dry_run_passes(conn, settings):
    settings.hyperliquid_testnet = True
    out = run_roundtrip(conn, settings, dry_run=True)
    assert out["passed"]
    rows = conn.execute(
        "SELECT passed_at FROM validation_runs WHERE id=?", (out["run_id"],)
    ).fetchone()
    assert rows["passed_at"] is not None


def test_testnet_refuses_mainnet_without_dry_run(conn, settings):
    settings.hyperliquid_testnet = False
    out = run_roundtrip(conn, settings, dry_run=False)
    assert not out["passed"]
    assert "mainnet" in (out["failure"] or "").lower()


def test_lookahead_audit_no_violations_on_empty(conn):
    out = audit(conn)
    assert out["n_violations"] == 0


def test_version_string_stable(settings):
    v1 = current_version_string(settings)
    v2 = current_version_string(settings)
    assert v1 == v2


def test_record_and_list_versions(conn, settings):
    v = record_version(conn, settings, notes="test")
    versions = list_versions(conn)
    assert any(x["version"] == v for x in versions)
