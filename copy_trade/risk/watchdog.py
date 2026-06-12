"""Per-vault drawdown watchdog.

Polls every 5min: for each active live subscription, if current equity
drop vs entry > threshold, emit emergency unsubscribe.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import structlog

from ..allocation.portfolio import AllocationDecision
from ..config import CopyTradeSettings, get_settings
from ..db import utc_now_iso
from ..execution.subscriptions import active_subscriptions

log = structlog.get_logger("copy_trade.watchdog")

LOCK_VAULT_BLOWUP = "vault_blowup"


def check_vault_drawdowns(conn: sqlite3.Connection,
                          settings: CopyTradeSettings | None = None,
                          equity_lookup=None) -> list[AllocationDecision]:
    """Return emergency unsubscribe decisions for any vault breaching threshold.

    `equity_lookup`: callable(uid) -> float current vault equity. If None,
    falls back to the last subscription_pnl row, allowing tests without
    a live connector.
    """
    settings = settings or get_settings()
    threshold = settings.per_vault_emergency_drawdown_pct
    decisions: list[AllocationDecision] = []

    for sub in active_subscriptions(conn):
        entry = sub.get("entry_equity_usdt") or 0.0
        if entry <= 0:
            continue
        current = None
        if equity_lookup is not None:
            try:
                current = float(equity_lookup(sub["master_uid"]))
            except Exception:  # noqa: BLE001
                current = None
        if current is None:
            row = conn.execute(
                "SELECT unrealized_pnl FROM subscription_pnl "
                "WHERE subscription_id=? ORDER BY id DESC LIMIT 1",
                (sub["id"],),
            ).fetchone()
            if row is None:
                continue
            current = float(sub["allocated_usdt"]) + float(row["unrealized_pnl"] or 0)

        drop = (current - float(sub["allocated_usdt"])) / float(sub["allocated_usdt"])
        if drop <= -threshold:
            log.warning("vault_blowup_detected", uid=sub["master_uid"], drop=drop)
            conn.execute(
                "INSERT INTO safety_events (event_at, event_type, detail) "
                "VALUES (?, 'vault_blowup', ?)",
                (utc_now_iso(),
                 f"uid={sub['master_uid']} drop={drop:.1%} threshold={threshold:.1%}"),
            )
            decisions.append(AllocationDecision(
                master_uid=sub["master_uid"], action="unsubscribe",
                current_usdt=float(sub["allocated_usdt"]), target_usdt=0.0,
                score=0.0, reason=f"watchdog_blowup_{drop:.1%}",
            ))
    return decisions
