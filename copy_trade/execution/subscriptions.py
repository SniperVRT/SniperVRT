"""Subscription management — translate allocation decisions into vault deposits.

On Hyperliquid:
  subscribe       → vault_usd_transfer(is_deposit=True,  usd=micro_usdc)
  unsubscribe     → vault_usd_transfer(is_deposit=False, usd=micro_usdc)
  rebalance up    → additional deposit
  rebalance down  → partial withdraw (BLOCKED if within 1-day lockup)

Safety layers:
  - Portfolio drawdown gate
  - Per-master min deposit floor (Hyperliquid enforces ~100 USDC server-side)
  - Lockup check before withdraw (24h on user vaults, 96h on HLP)
  - Idempotent: don't double-subscribe an already-active master
  - Full audit trail in `subscriptions` table
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
from ..platforms.hyperliquid import HyperliquidConnector

log = structlog.get_logger("copy_trade.subscriptions")

USER_VAULT_LOCKUP_MS = 24 * 60 * 60 * 1000  # 24h
PLATFORM = "hyperliquid"


def vault_equity_from_db(conn: sqlite3.Connection, uid: str) -> float | None:
    """Latest known vault equity from the most recent snapshot's raw_json.

    Used to stamp entry_equity_usdt at subscribe time without an API call,
    which the watchdog later compares against to detect blowups.
    """
    row = conn.execute(
        "SELECT raw_json FROM trader_snapshots WHERE master_uid=? "
        "ORDER BY captured_at DESC LIMIT 1", (uid,),
    ).fetchone()
    if not row or not row["raw_json"]:
        return None
    try:
        raw = json.loads(row["raw_json"])
        for entry in (raw.get("portfolio") or []):
            if isinstance(entry, list) and len(entry) == 2 and entry[0] == "allTime":
                hist = (entry[1] or {}).get("accountValueHistory") or []
                if hist:
                    return float(hist[-1][1])
    except Exception:  # noqa: BLE001
        return None
    return None


def active_subscriptions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Active LIVE subscriptions only. Paper subs are isolated by mode."""
    rows = conn.execute(
        "SELECT s.*, m.nickname FROM subscriptions s "
        "JOIN masters m ON m.uid = s.master_uid "
        "WHERE s.status = 'active' AND s.mode = 'live'"
    ).fetchall()
    return [dict(r) for r in rows]


def execute_decisions(
    conn: sqlite3.Connection,
    decisions: list[AllocationDecision],
    connector: HyperliquidConnector | None = None,
    settings: CopyTradeSettings | None = None,
    dry_run: bool = True,
) -> dict[str, int]:
    """Execute allocation decisions against Hyperliquid vault API.

    `dry_run=True` (default) logs intent but makes no chain transactions.
    Set `dry_run=False` only after testing on testnet first.
    """
    settings = settings or get_settings()
    counts = {"subscribe": 0, "rebalance": 0, "unsubscribe": 0,
              "keep": 0, "lockup_blocked": 0, "error": 0}

    if not _safety_gate_ok(conn, settings):
        log.warning("subscriptions_blocked_safety_gate")
        # Optional emergency unwind: forced withdrawals (still respect lockup).
        if settings.emergency_force_unwind:
            decisions = _force_unwind_decisions(conn)
            log.warning("emergency_unwind_engaged", n=len(decisions))
        else:
            return counts

    # Only build the connector if we'll actually call it.
    if connector is None and not dry_run:
        connector = HyperliquidConnector(settings)

    # Cap single-tick capital movement: clip target_usdt deltas to keep
    # rebalances from accidentally swinging the portfolio in one cycle.
    decisions = _clip_deltas(decisions, settings)

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
                if d.target_usdt < settings.min_allocation_usdt:
                    log.info("skip_subscribe_below_min", uid=d.master_uid,
                             usdt=d.target_usdt)
                    continue
                log.info("subscribe", uid=d.master_uid, usdt=d.target_usdt,
                         dry_run=dry_run)
                ext_id, lockup_until = "", _lockup_ts()
                if not dry_run:
                    result = connector.follow(
                        d.master_uid, allocated_usdt=d.target_usdt,
                    )
                    ext_id = _extract_tx_id(result)
                entry_eq = vault_equity_from_db(conn, d.master_uid)
                conn.execute(
                    """
                    INSERT INTO subscriptions
                        (master_uid, platform, mode, allocated_usdt, subscribed_at,
                         status, external_sub_id, lockup_until_ms, entry_equity_usdt)
                    VALUES (?, ?, 'live', ?, ?, 'active', ?, ?, ?)
                    """,
                    (d.master_uid, PLATFORM, d.target_usdt, utc_now_iso(),
                     ext_id, lockup_until, entry_eq if entry_eq else d.target_usdt),
                )
                counts["subscribe"] += 1

            elif d.action == "rebalance":
                sub = active.get(d.master_uid)
                if sub is None:
                    continue
                delta = d.target_usdt - d.current_usdt
                if delta > 0:
                    # Deposit more (no lockup issue)
                    log.info("rebalance_up", uid=d.master_uid, delta=delta,
                             dry_run=dry_run)
                    if not dry_run:
                        connector.follow(d.master_uid, allocated_usdt=delta)
                    conn.execute(
                        "UPDATE subscriptions SET allocated_usdt=?, "
                        "lockup_until_ms=? WHERE id=?",
                        (d.target_usdt, _lockup_ts(), sub["id"]),
                    )
                    counts["rebalance"] += 1
                elif delta < 0:
                    # Withdraw — blocked if within lockup
                    if not _can_withdraw(sub):
                        log.info("rebalance_blocked_lockup", uid=d.master_uid)
                        counts["lockup_blocked"] += 1
                        continue
                    log.info("rebalance_down", uid=d.master_uid, delta=delta,
                             dry_run=dry_run)
                    if not dry_run:
                        connector.unfollow(d.master_uid, withdraw_usdt=abs(delta))
                    conn.execute(
                        "UPDATE subscriptions SET allocated_usdt=? WHERE id=?",
                        (d.target_usdt, sub["id"]),
                    )
                    counts["rebalance"] += 1

            elif d.action == "unsubscribe":
                sub = active.get(d.master_uid)
                if sub is None:
                    counts["unsubscribe"] += 1
                    continue
                if not _can_withdraw(sub):
                    log.info("unsubscribe_blocked_lockup", uid=d.master_uid)
                    counts["lockup_blocked"] += 1
                    continue
                log.info("unsubscribe", uid=d.master_uid, dry_run=dry_run)
                if not dry_run:
                    connector.unfollow(
                        d.master_uid, withdraw_usdt=float(sub["allocated_usdt"]),
                    )
                conn.execute(
                    "UPDATE subscriptions SET status='unsubscribed', "
                    "unsubscribed_at=? WHERE id=?",
                    (utc_now_iso(), sub["id"]),
                )
                counts["unsubscribe"] += 1

        except Exception as e:  # noqa: BLE001
            log.error("subscription_error", uid=d.master_uid,
                      action=d.action, err=str(e))
            counts["error"] += 1

    return counts


def snapshot_pnl(conn: sqlite3.Connection,
                 connector: HyperliquidConnector | None = None,
                 settings: CopyTradeSettings | None = None) -> None:
    """Poll vault equity for all active subscriptions and persist."""
    settings = settings or get_settings()
    connector = connector or HyperliquidConnector(settings)
    equities = {
        str(e.get("vaultAddress", "")).lower(): e
        for e in connector.my_vault_equities()
    }
    for sub in active_subscriptions(conn):
        try:
            uid_lower = sub["master_uid"].lower()
            entry = equities.get(uid_lower, {})
            current_equity = float(entry.get("equity", 0) or 0)
            allocated = float(sub["allocated_usdt"])
            unrealised = current_equity - allocated
            # Hyperliquid vault PnL is purely unrealised until withdrawal.
            conn.execute(
                """
                INSERT INTO subscription_pnl
                    (subscription_id, captured_at, realized_pnl,
                     unrealized_pnl, total_pnl, raw_json)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    sub["id"], utc_now_iso(),
                    0.0, unrealised, unrealised,
                    json.dumps(entry),
                ),
            )
            # Update lockup if Hyperliquid extended it
            if entry.get("lockedUntilTimestamp"):
                conn.execute(
                    "UPDATE subscriptions SET lockup_until_ms=? WHERE id=?",
                    (int(entry["lockedUntilTimestamp"]), sub["id"]),
                )
        except Exception as e:  # noqa: BLE001
            log.warning("pnl_poll_failed", sub=sub.get("id"), err=str(e))


def _safety_gate_ok(conn: sqlite3.Connection, settings: CopyTradeSettings) -> bool:  # noqa: D401
    """Shared safety gate used by live + paper executors."""
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


def _can_withdraw(sub: dict[str, Any]) -> bool:
    lockup = sub.get("lockup_until_ms")
    if lockup is None:
        return True
    return int(time.time() * 1000) >= int(lockup)


def _lockup_ts() -> int:
    return int(time.time() * 1000) + USER_VAULT_LOCKUP_MS


def _force_unwind_decisions(conn: sqlite3.Connection) -> list[AllocationDecision]:
    """Emergency: withdraw everything we can (subject to lockup)."""
    return [
        AllocationDecision(
            master_uid=r["master_uid"], action="unsubscribe",
            current_usdt=float(r["allocated_usdt"]), target_usdt=0.0,
            score=0.0, reason="emergency_force_unwind",
        )
        for r in active_subscriptions(conn)
    ]


def _clip_deltas(decisions: list[AllocationDecision],
                 settings: CopyTradeSettings) -> list[AllocationDecision]:
    """Limit single-tick allocation changes to max_delta_per_rebalance_pct of capital."""
    cap = settings.total_capital_usdt * settings.max_delta_per_rebalance_pct
    out: list[AllocationDecision] = []
    for d in decisions:
        delta = d.target_usdt - d.current_usdt
        if abs(delta) > cap:
            clipped = d.current_usdt + (cap if delta > 0 else -cap)
            out.append(AllocationDecision(
                master_uid=d.master_uid, action=d.action,
                current_usdt=d.current_usdt, target_usdt=clipped,
                score=d.score, reason=f"{d.reason}|clipped",
            ))
        else:
            out.append(d)
    return out


def _extract_tx_id(result: Any) -> str:
    """Pull tx hash from a Hyperliquid action response if present."""
    if not isinstance(result, dict):
        return ""
    response = result.get("response") or {}
    data = response.get("data") if isinstance(response, dict) else {}
    if isinstance(data, dict):
        statuses = data.get("statuses") or []
        if statuses and isinstance(statuses[0], dict):
            return str(statuses[0].get("tx", ""))
    return ""
