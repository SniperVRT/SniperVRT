"""Hard risk engine. Every check must pass before a signal can be promoted
to an order — even in paper mode.

Caps come from `Settings` so they can be tuned per environment without code
changes. Default profile is the "nano" bankroll described in the spec:
$1–$5 positions, $10 daily stop, 3 open positions, manual approval.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..config import Settings


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str | None = None
    detail: dict | None = None

    @classmethod
    def ok(cls) -> "RiskDecision":
        return cls(True)

    @classmethod
    def block(cls, reason: str, **detail) -> "RiskDecision":
        return cls(False, reason, detail or None)


# --------------------------------------------------------------------------- #
# Read helpers (cheap aggregates against the SQLite store)
# --------------------------------------------------------------------------- #
def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _week_start_iso() -> str:
    today = datetime.now(timezone.utc).date()
    return (today - timedelta(days=today.weekday())).isoformat()


def realized_pnl_today(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT COALESCE(realized_usd,0) FROM pnl_history WHERE day = ?",
        (_today_iso(),),
    ).fetchone()
    return float(row[0]) if row else 0.0


def realized_pnl_week(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(realized_usd),0) FROM pnl_history WHERE day >= ?",
        (_week_start_iso(),),
    ).fetchone()
    return float(row[0]) if row else 0.0


def open_position_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE closed_at IS NULL",
    ).fetchone()
    return int(row[0]) if row else 0


def has_open_position(conn: sqlite3.Connection, ticker: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM positions WHERE ticker = ? AND closed_at IS NULL LIMIT 1",
        (ticker,),
    ).fetchone()
    return row is not None


def last_loss_at(conn: sqlite3.Connection) -> datetime | None:
    row = conn.execute(
        "SELECT created_at FROM risk_events WHERE kind = 'loss' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    try:
        return datetime.fromisoformat(row[0])
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# The single entry point used by the scanner / executor
# --------------------------------------------------------------------------- #
def can_take_trade(
    *,
    conn: sqlite3.Connection,
    ticker: str,
    proposed_size_usd: float,
    settings: Settings,
    now: datetime | None = None,
) -> RiskDecision:
    now = now or datetime.now(timezone.utc)

    if proposed_size_usd <= 0:
        return RiskDecision.block("size_nonpositive")
    if proposed_size_usd > settings.max_position_usd:
        return RiskDecision.block(
            "exceeds_max_position",
            proposed=proposed_size_usd,
            cap=settings.max_position_usd,
        )

    # Daily / weekly stops are stored as negative numbers (losses).
    day_pnl = realized_pnl_today(conn)
    if day_pnl <= -settings.daily_loss_stop_usd:
        return RiskDecision.block("daily_loss_stop_hit", day_pnl=day_pnl)

    week_pnl = realized_pnl_week(conn)
    if week_pnl <= -settings.weekly_loss_stop_usd:
        return RiskDecision.block("weekly_loss_stop_hit", week_pnl=week_pnl)

    if open_position_count(conn) >= settings.max_open_positions:
        return RiskDecision.block("max_open_positions")

    if has_open_position(conn, ticker):
        # Spec: no averaging down unless explicitly allowed.
        return RiskDecision.block("position_already_open", ticker=ticker)

    last_loss = last_loss_at(conn)
    if last_loss is not None:
        cooldown_until = last_loss + timedelta(minutes=settings.cooldown_minutes_after_loss)
        if now < cooldown_until:
            return RiskDecision.block(
                "cooldown_after_loss",
                cooldown_until=cooldown_until.isoformat(),
            )

    return RiskDecision.ok()


def record_risk_event(conn: sqlite3.Connection, kind: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO risk_events (kind, detail, created_at) VALUES (?, ?, ?)",
        (kind, detail, datetime.now(timezone.utc).isoformat()),
    )
