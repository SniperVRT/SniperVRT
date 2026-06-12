"""Tests for per-vault drawdown watchdog."""

from __future__ import annotations

from copy_trade.risk.watchdog import check_vault_drawdowns
from copy_trade.platforms.base import MasterStats
from copy_trade.traders.store import upsert_master
from copy_trade.db import utc_now_iso


def _seed_sub(conn, uid="v1", allocated=100.0, entry_eq=1000.0):
    upsert_master(conn, MasterStats(
        uid=uid, platform="hyperliquid", nickname="",
        roi_7d=None, roi_30d=None, roi_all=None, mdd=None,
        win_rate=None, total_trades=None, followers=None,
        aum_usdt=None, avg_holding_h=None, sharpe=None,
    ))
    conn.execute(
        "INSERT INTO subscriptions (master_uid, platform, mode, allocated_usdt, "
        "subscribed_at, status, external_sub_id, lockup_until_ms, entry_equity_usdt) "
        "VALUES (?, 'hyperliquid', 'live', ?, ?, 'active', '', 0, ?)",
        (uid, allocated, utc_now_iso(), entry_eq),
    )


def test_watchdog_triggers_on_big_drop(conn, settings):
    _seed_sub(conn, "v1", allocated=100.0, entry_eq=1000.0)
    # Synthetic equity lookup: vault dropped 30%
    lookup = lambda _uid: 70.0  # allocated is 100 → drop = -30%
    settings.per_vault_emergency_drawdown_pct = 0.20
    decisions = check_vault_drawdowns(conn, settings, equity_lookup=lookup)
    assert len(decisions) == 1
    assert decisions[0].action == "unsubscribe"


def test_watchdog_silent_below_threshold(conn, settings):
    _seed_sub(conn, "v1", allocated=100.0)
    lookup = lambda _uid: 95.0  # only -5% drop
    settings.per_vault_emergency_drawdown_pct = 0.20
    decisions = check_vault_drawdowns(conn, settings, equity_lookup=lookup)
    assert decisions == []
