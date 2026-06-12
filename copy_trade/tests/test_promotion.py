"""Tests for promotion gates."""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from copy_trade.validation.promotion import check_ready_for_live
from copy_trade.config import CopyTradeSettings


def _live_settings(**kw) -> CopyTradeSettings:
    base = dict(
        db_path=":memory:", live_enabled=True, live_dry_run=False,
        live_require_manual_approval=True, emergency_force_unwind=True,
    )
    base.update(kw)
    return CopyTradeSettings(**base)


def test_fresh_db_has_all_blockers(conn):
    s = _live_settings()
    rep = check_ready_for_live(conn, s)
    assert not rep.ready
    assert any("history_days" in b for b in rep.blockers)
    assert any("paper_runs_7d" in b for b in rep.blockers)
    assert any("spearman" in b or "testnet" in b for b in rep.blockers)


def test_emergency_unwind_off_blocks(conn):
    s = _live_settings(emergency_force_unwind=False)
    rep = check_ready_for_live(conn, s)
    assert "emergency_force_unwind=False" in rep.blockers


def test_live_dry_run_blocks(conn):
    s = _live_settings(live_dry_run=True)
    rep = check_ready_for_live(conn, s)
    assert "live_dry_run=True" in rep.blockers


def test_testnet_roundtrip_recent_clears_one_blocker(conn):
    s = _live_settings()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO validation_runs (kind, started_at, passed_at, details_json) "
        "VALUES ('testnet_roundtrip', ?, ?, '{}')",
        (now, now),
    )
    rep = check_ready_for_live(conn, s)
    assert "testnet_roundtrip_missing_or_stale" not in rep.blockers
