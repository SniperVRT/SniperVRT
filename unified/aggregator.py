"""Aggregate Kalshi + copy-trade state into unified snapshots."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .db import connect, copytrade_db_path, kalshi_db_path, utc_now_iso


def _open(p: Path) -> sqlite3.Connection | None:
    if not p.exists():
        return None
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    return c


def aggregate(unified_conn: sqlite3.Connection | None = None,
              total_capital: float = 0.0) -> dict:
    unified_conn = unified_conn or connect()
    kc = _open(kalshi_db_path())
    cc = _open(copytrade_db_path())

    kalshi_equity = kalshi_deployed = 0.0
    open_signals = 0
    kalshi_locks: list[str] = []
    if kc is not None:
        try:
            row = kc.execute(
                "SELECT * FROM pnl_history ORDER BY date_iso DESC LIMIT 1"
            ).fetchone()
            if row:
                kalshi_equity = float(row["bankroll_usd"] or 0)
        except Exception:  # noqa: BLE001
            pass
        try:
            open_signals = kc.execute(
                "SELECT COUNT(*) FROM signals WHERE status='pending'"
            ).fetchone()[0]
        except Exception:  # noqa: BLE001
            pass
        try:
            kalshi_locks = [
                r["name"] for r in kc.execute(
                    "SELECT name FROM governance_locks WHERE released_at IS NULL"
                ).fetchall()
            ]
        except Exception:  # noqa: BLE001
            pass
        kc.close()

    copy_equity = copy_deployed = 0.0
    active_subs = 0
    copy_events: list[str] = []
    if cc is not None:
        try:
            row = cc.execute(
                "SELECT * FROM portfolio_snapshots ORDER BY captured_at DESC LIMIT 1"
            ).fetchone()
            if row:
                copy_equity = (float(row["total_capital"] or 0)
                                + float(row["realized_pnl"] or 0)
                                + float(row["unrealized_pnl"] or 0))
                copy_deployed = float(row["deployed_usdt"] or 0)
        except Exception:  # noqa: BLE001
            pass
        try:
            active_subs = cc.execute(
                "SELECT COUNT(*) FROM subscriptions WHERE status='active' AND mode='live'"
            ).fetchone()[0]
        except Exception:  # noqa: BLE001
            pass
        try:
            copy_events = [
                r["event_type"] for r in cc.execute(
                    "SELECT event_type FROM safety_events WHERE resolved_at IS NULL"
                ).fetchall()
            ]
        except Exception:  # noqa: BLE001
            pass
        cc.close()

    total = kalshi_equity + copy_equity
    peak_row = unified_conn.execute(
        "SELECT MAX(total_equity) AS p FROM unified_snapshots"
    ).fetchone()
    peak = max(total, float(peak_row["p"] or total))
    dd_pct = (peak - total) / peak if peak > 0 else 0.0

    locks = {"kalshi": kalshi_locks, "copytrade": copy_events}
    unified_conn.execute(
        "INSERT INTO unified_snapshots (captured_at, kalshi_equity, "
        "copytrade_equity, total_equity, kalshi_deployed, copytrade_deployed, "
        "total_drawdown_pct, combined_locks_json, active_subscriptions_n, "
        "open_signals_n) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (utc_now_iso(), kalshi_equity, copy_equity, total,
         kalshi_deployed, copy_deployed, dd_pct, json.dumps(locks),
         active_subs, open_signals),
    )
    return {"kalshi_equity": kalshi_equity, "copy_equity": copy_equity,
            "total_equity": total, "drawdown_pct": dd_pct, "locks": locks,
            "active_subs": active_subs, "open_signals": open_signals}
