"""Paper-trading executor for Hyperliquid vaults.

Records subscribe/unsubscribe decisions in the same `subscriptions` table
with `mode='paper'`, but makes NO real API calls. PnL is tracked by
polling the *real* vault's equity curve and scaling by our virtual share:

    our_paper_pnl = (current_vault_equity / vault_tvl_at_entry - 1) × allocated_usdt

This gives true-to-life PnL without risking capital. Same scoring,
same allocation logic, same DB — the only difference from live is the
connector. So when paper passes, live is one config flag away.

Lockup is still enforced (24h) so paper behaves identically to live.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

import structlog

from ..allocation.portfolio import AllocationDecision
from ..config import CopyTradeSettings, get_settings
from ..db import utc_now_iso
from ..execution.subscriptions import (
    USER_VAULT_LOCKUP_MS, _can_withdraw, _lockup_ts, _safety_gate_ok,
)
from ..platforms.hyperliquid import HyperliquidConnector

log = structlog.get_logger("copy_trade.paper")


def active_paper_subscriptions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT s.*, m.nickname FROM subscriptions s "
        "JOIN masters m ON m.uid = s.master_uid "
        "WHERE s.status = 'active' AND s.mode = 'paper'"
    ).fetchall()
    return [dict(r) for r in rows]


def execute_paper_decisions(
    conn: sqlite3.Connection,
    decisions: list[AllocationDecision],
    settings: CopyTradeSettings | None = None,
) -> dict[str, int]:
    """Apply allocation decisions in paper mode — no real API calls."""
    settings = settings or get_settings()
    counts = {"subscribe": 0, "rebalance": 0, "unsubscribe": 0,
              "keep": 0, "lockup_blocked": 0, "error": 0}

    if not _safety_gate_ok(conn, settings):
        log.warning("paper_blocked_safety_gate")
        return counts

    active = {r["master_uid"]: r for r in active_paper_subscriptions(conn)}

    for d in decisions:
        try:
            if d.action == "keep":
                counts["keep"] += 1
                continue

            if d.action == "subscribe":
                if d.master_uid in active:
                    counts["keep"] += 1
                    continue
                if d.target_usdt < settings.min_allocation_usdt:
                    continue
                conn.execute(
                    """
                    INSERT INTO subscriptions
                        (master_uid, platform, mode, allocated_usdt, subscribed_at,
                         status, external_sub_id, lockup_until_ms)
                    VALUES (?, 'hyperliquid', 'paper', ?, ?, 'active', '', ?)
                    """,
                    (d.master_uid, d.target_usdt, utc_now_iso(), _lockup_ts()),
                )
                log.info("paper_subscribe", uid=d.master_uid, usdt=d.target_usdt)
                counts["subscribe"] += 1

            elif d.action == "rebalance":
                sub = active.get(d.master_uid)
                if sub is None:
                    continue
                delta = d.target_usdt - d.current_usdt
                if delta < 0 and not _can_withdraw(sub):
                    counts["lockup_blocked"] += 1
                    continue
                conn.execute(
                    "UPDATE subscriptions SET allocated_usdt=?, lockup_until_ms=? "
                    "WHERE id=?",
                    (d.target_usdt,
                     _lockup_ts() if delta > 0 else sub["lockup_until_ms"],
                     sub["id"]),
                )
                counts["rebalance"] += 1

            elif d.action == "unsubscribe":
                sub = active.get(d.master_uid)
                if sub is None:
                    counts["unsubscribe"] += 1
                    continue
                if not _can_withdraw(sub):
                    counts["lockup_blocked"] += 1
                    continue
                conn.execute(
                    "UPDATE subscriptions SET status='unsubscribed', "
                    "unsubscribed_at=? WHERE id=?",
                    (utc_now_iso(), sub["id"]),
                )
                counts["unsubscribe"] += 1

        except Exception as e:  # noqa: BLE001
            log.error("paper_error", uid=d.master_uid, action=d.action, err=str(e))
            counts["error"] += 1

    return counts


def snapshot_paper_pnl(
    conn: sqlite3.Connection,
    connector: HyperliquidConnector | None = None,
    settings: CopyTradeSettings | None = None,
) -> None:
    """Compute paper PnL by polling the real vault's equity curve.

    For each active paper subscription, fetch the vault's current equity
    curve from Hyperliquid. PnL = allocation × (current_share_price / entry_share_price - 1).
    Entry share price is captured from the vault's equity at subscribe time
    (stored in the first snapshot's raw_json), so paper tracks real performance.
    """
    settings = settings or get_settings()
    connector = connector or HyperliquidConnector(settings)

    for sub in active_paper_subscriptions(conn):
        try:
            d = connector.vault_details(sub["master_uid"])
            current_equity = _latest_equity(d)
            entry_equity = _entry_equity(conn, sub) or current_equity
            if entry_equity <= 0:
                continue
            pnl_ratio = (current_equity / entry_equity) - 1.0
            unrealised = float(sub["allocated_usdt"]) * pnl_ratio
            conn.execute(
                """
                INSERT INTO subscription_pnl
                    (subscription_id, captured_at, realized_pnl,
                     unrealized_pnl, total_pnl, raw_json)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    sub["id"], utc_now_iso(), 0.0, unrealised, unrealised,
                    json.dumps({"current_equity": current_equity,
                                "entry_equity": entry_equity,
                                "pnl_ratio": pnl_ratio}),
                ),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("paper_pnl_failed", sub=sub.get("id"), err=str(e))


def _latest_equity(vault_details: dict) -> float:
    """Pull the last value from any accountValueHistory series in vaultDetails."""
    portfolio = vault_details.get("portfolio") or []
    for entry in portfolio:
        if isinstance(entry, list) and len(entry) == 2 and entry[0] == "allTime":
            history = (entry[1] or {}).get("accountValueHistory") or []
            if history:
                try:
                    return float(history[-1][1])
                except (TypeError, ValueError, IndexError):
                    pass
    return 0.0


def _entry_equity(conn: sqlite3.Connection, sub: dict) -> float | None:
    """Equity at subscription time, persisted in the first PnL snapshot's raw."""
    row = conn.execute(
        "SELECT raw_json FROM subscription_pnl WHERE subscription_id=? "
        "ORDER BY id ASC LIMIT 1",
        (sub["id"],),
    ).fetchone()
    if row is None:
        return None
    try:
        return float(json.loads(row["raw_json"]).get("entry_equity", 0))
    except Exception:  # noqa: BLE001
        return None
