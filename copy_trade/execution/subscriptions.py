"""Subscription management — wire allocation decisions to platform API calls.

This is the money-making module. It translates AllocationDecisions into
actual subscribe/unsubscribe calls on the platform, with:
  - Safety gate: check portfolio drawdown before any new subscribe
  - Idempotency: don't double-subscribe the same master
  - Full audit trail in `subscriptions` table
  - Graceful error handling with retry logic inside the connector
"""

from __future__ import annotations

import sqlite3
from typing import Any

import structlog

from ..allocation.portfolio import AllocationDecision
from ..config import CopyTradeSettings, get_settings
from ..db import utc_now_iso
from ..platforms.bitget import BitgetConnector

log = structlog.get_logger("copy_trade.subscriptions")


def active_subscriptions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT s.*, m.nickname FROM subscriptions s "
        "JOIN masters m ON m.uid = s.master_uid "
        "WHERE s.status = 'active'"
    ).fetchall()
    return [dict(r) for r in rows]


def execute_decisions(
    conn: sqlite3.Connection,
    decisions: list[AllocationDecision],
    connector: BitgetConnector | None = None,
    settings: CopyTradeSettings | None = None,
    dry_run: bool = True,
) -> dict[str, int]:
    """Execute a list of allocation decisions against the platform API.

    `dry_run=True` (default): logs intent but makes no API calls.
    Set `dry_run=False` with live API key to go live.

    Returns counts: {subscribe: N, rebalance: N, unsubscribe: N, keep: N, error: N}
    """
    settings = settings or get_settings()
    connector = connector or BitgetConnector(settings)
    counts: dict[str, int] = {"subscribe": 0, "rebalance": 0,
                               "unsubscribe": 0, "keep": 0, "error": 0}

    # Safety: check portfolio drawdown gate
    if not _safety_gate_ok(conn, settings):
        log.warning("subscriptions_blocked_safety_gate")
        return counts

    active = {r["master_uid"]: r for r in active_subscriptions(conn)}

    for d in decisions:
        try:
            if d.action == "keep":
                counts["keep"] += 1
                continue

            if d.action == "subscribe":
                if d.master_uid in active:
                    log.info("skip_subscribe_already_active", uid=d.master_uid)
                    counts["keep"] += 1
                    continue
                log.info("subscribe", uid=d.master_uid, usdt=d.target_usdt,
                         dry_run=dry_run)
                ext_id = ""
                if not dry_run:
                    ext_id = connector.subscribe(
                        d.master_uid, allocated_usdt=d.target_usdt,
                    )
                conn.execute(
                    """
                    INSERT INTO subscriptions
                        (master_uid, platform, allocated_usdt, subscribed_at,
                         status, external_sub_id)
                    VALUES (?, 'bybit', ?, ?, 'active', ?)
                    """,
                    (d.master_uid, d.target_usdt, utc_now_iso(), ext_id),
                )
                counts["subscribe"] += 1

            elif d.action == "rebalance":
                log.info("rebalance", uid=d.master_uid,
                         from_usdt=d.current_usdt, to_usdt=d.target_usdt,
                         dry_run=dry_run)
                if not dry_run:
                    sub = active.get(d.master_uid)
                    if sub and sub.get("external_sub_id"):
                        # Bybit: cancel + recreate at new allocation
                        connector.unsubscribe(d.master_uid, sub["external_sub_id"])
                        ext_id = connector.subscribe(
                            d.master_uid, allocated_usdt=d.target_usdt,
                        )
                        conn.execute(
                            "UPDATE subscriptions SET allocated_usdt=?, "
                            "external_sub_id=?, updated_at=? WHERE id=?",
                            (d.target_usdt, ext_id, utc_now_iso(), sub["id"]),
                        )
                counts["rebalance"] += 1

            elif d.action == "unsubscribe":
                log.info("unsubscribe", uid=d.master_uid, dry_run=dry_run)
                sub = active.get(d.master_uid)
                if sub:
                    if not dry_run and sub.get("external_sub_id"):
                        connector.unsubscribe(d.master_uid, sub["external_sub_id"])
                    conn.execute(
                        "UPDATE subscriptions SET status='unsubscribed', "
                        "unsubscribed_at=? WHERE id=?",
                        (utc_now_iso(), sub["id"]),
                    )
                counts["unsubscribe"] += 1

        except Exception as e:  # noqa: BLE001
            log.error("subscription_error", uid=d.master_uid, action=d.action, err=str(e))
            counts["error"] += 1

    return counts


def _safety_gate_ok(conn: sqlite3.Connection, settings: CopyTradeSettings) -> bool:
    """Return False if portfolio drawdown exceeds the emergency-stop threshold."""
    row = conn.execute(
        "SELECT drawdown_pct FROM portfolio_snapshots "
        "ORDER BY captured_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return True
    drawdown = float(row["drawdown_pct"])
    if drawdown >= settings.max_total_drawdown_pct:
        conn.execute(
            "INSERT INTO safety_events (event_at, event_type, detail) VALUES (?,?,?)",
            (utc_now_iso(), "drawdown_stop",
             f"drawdown={drawdown:.1%}>={settings.max_total_drawdown_pct:.1%}"),
        )
        return False
    return True


def snapshot_pnl(conn: sqlite3.Connection,
                 connector: BitgetConnector | None = None,
                 settings: CopyTradeSettings | None = None) -> None:
    """Poll PnL for all active subscriptions and persist."""
    settings = settings or get_settings()
    connector = connector or BitgetConnector(settings)
    for sub in active_subscriptions(conn):
        try:
            pnl = connector.subscription_pnl(sub.get("external_sub_id", ""))
            realized = float(pnl.get("realizedPnl", 0) or 0)
            unrealized = float(pnl.get("unrealisedPnl", 0) or 0)
            import json
            conn.execute(
                """
                INSERT INTO subscription_pnl
                    (subscription_id, captured_at, realized_pnl,
                     unrealized_pnl, total_pnl, raw_json)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    sub["id"], utc_now_iso(),
                    realized, unrealized, realized + unrealized,
                    json.dumps(pnl),
                ),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("pnl_poll_failed", sub=sub.get("id"), err=str(e))
