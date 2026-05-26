"""Portfolio-level governance + emergency locks.

Sits ABOVE the per-trade `risk.rules`. The scanner / executor call
`evaluate_portfolio()` before every prospective trade. Any lock in
`governance_locks` immediately blocks execution until released.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from ..config import Settings
from ..db import utc_now_iso


# Named locks the system can engage / release.
LOCK_STALE_DATA = "stale_data"
LOCK_REPEAT_FAILURE = "repeat_failure"
LOCK_RECONCILE_FAIL = "reconcile_fail"
LOCK_EMERGENCY = "emergency"
LOCK_DB_CORRUPTION = "db_corruption"
LOCK_DRAWDOWN = "portfolio_drawdown"
LOCK_CONSECUTIVE_LOSSES = "consecutive_losses"
LOCK_EXPOSURE = "exposure_cap"
LOCK_DUPLICATE_ORDER = "duplicate_order"

ALL_LOCKS = (
    LOCK_STALE_DATA, LOCK_REPEAT_FAILURE, LOCK_RECONCILE_FAIL, LOCK_EMERGENCY,
    LOCK_DB_CORRUPTION, LOCK_DRAWDOWN, LOCK_CONSECUTIVE_LOSSES,
    LOCK_EXPOSURE, LOCK_DUPLICATE_ORDER,
)


@dataclass(frozen=True)
class PortfolioState:
    bankroll_usd: float
    realized_pnl_usd: float
    unrealized_pnl_usd: float
    open_exposure_usd: float
    open_positions: int
    peak_equity_usd: float
    drawdown_usd: float
    category_exposure: dict[str, float]
    strategy_exposure: dict[str, float]
    consecutive_losses: int


@dataclass(frozen=True)
class GovernanceDecision:
    allowed: bool
    reason: str | None = None
    detail: dict | None = None
    engaged_locks: list[str] | None = None


# --------------------------------------------------------------------------- #
# Locks
# --------------------------------------------------------------------------- #
def engage_lock(conn: sqlite3.Connection, name: str, reason: str) -> None:
    conn.execute(
        """
        INSERT INTO governance_locks (name, engaged, reason, engaged_at)
        VALUES (?, 1, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            engaged=1, reason=excluded.reason,
            engaged_at=excluded.engaged_at, released_at=NULL
        """,
        (name, reason, utc_now_iso()),
    )


def release_lock(conn: sqlite3.Connection, name: str) -> None:
    conn.execute(
        "UPDATE governance_locks SET engaged=0, released_at=? WHERE name=?",
        (utc_now_iso(), name),
    )


def active_locks(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT name, reason FROM governance_locks WHERE engaged = 1"
    ).fetchall()
    return [(r["name"], r["reason"] or "") for r in rows]


def is_locked(conn: sqlite3.Connection) -> bool:
    return bool(active_locks(conn))


# --------------------------------------------------------------------------- #
# Portfolio aggregation
# --------------------------------------------------------------------------- #
def _paper_open(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT pp.id, pp.ticker, pp.side, pp.qty, pp.avg_cost_cents,
               COALESCE(pp.last_mark_cents, pp.avg_cost_cents) AS mark,
               pp.strategy, m.category
          FROM paper_positions pp
          LEFT JOIN markets m ON m.ticker = pp.ticker
         WHERE pp.closed_at IS NULL
        """,
    ).fetchall()


def _consecutive_losses(conn: sqlite3.Connection, n: int = 5) -> int:
    rows = conn.execute(
        "SELECT realized_pnl_usd FROM paper_positions "
        "WHERE realized_pnl_usd IS NOT NULL "
        "ORDER BY closed_at DESC LIMIT ?", (n,),
    ).fetchall()
    streak = 0
    for r in rows:
        if (r["realized_pnl_usd"] or 0) < 0:
            streak += 1
        else:
            break
    return streak


def _peak_equity(conn: sqlite3.Connection, bankroll: float) -> float:
    row = conn.execute(
        "SELECT MAX(peak_equity_usd) FROM portfolio_state"
    ).fetchone()
    return max(bankroll, float(row[0] or 0.0))


def compute_state(conn: sqlite3.Connection, settings: Settings) -> PortfolioState:
    rows = _paper_open(conn)
    cat_exp: dict[str, float] = {}
    strat_exp: dict[str, float] = {}
    open_exp = 0.0
    unrealized = 0.0
    for r in rows:
        notional = r["qty"] * r["avg_cost_cents"] / 100.0
        open_exp += notional
        mark_pnl = (r["mark"] - r["avg_cost_cents"]) * r["qty"] / 100.0
        unrealized += mark_pnl
        if r["category"]:
            cat_exp[r["category"]] = cat_exp.get(r["category"], 0.0) + notional
        if r["strategy"]:
            strat_exp[r["strategy"]] = strat_exp.get(r["strategy"], 0.0) + notional

    realized = float(conn.execute(
        "SELECT COALESCE(SUM(realized_usd), 0) FROM paper_pnl"
    ).fetchone()[0] or 0.0)

    equity = settings.bankroll_usd + realized + unrealized
    peak = _peak_equity(conn, equity)
    drawdown = max(0.0, peak - equity)

    return PortfolioState(
        bankroll_usd=settings.bankroll_usd,
        realized_pnl_usd=realized,
        unrealized_pnl_usd=unrealized,
        open_exposure_usd=open_exp,
        open_positions=len(rows),
        peak_equity_usd=peak,
        drawdown_usd=drawdown,
        category_exposure=cat_exp,
        strategy_exposure=strat_exp,
        consecutive_losses=_consecutive_losses(conn),
    )


def snapshot(conn: sqlite3.Connection, state: PortfolioState) -> None:
    conn.execute(
        """
        INSERT INTO portfolio_state (
            captured_at, bankroll_usd, realized_pnl_usd, unrealized_pnl_usd,
            open_exposure_usd, open_positions, peak_equity_usd, drawdown_usd,
            payload_json
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            utc_now_iso(), state.bankroll_usd, state.realized_pnl_usd,
            state.unrealized_pnl_usd, state.open_exposure_usd,
            state.open_positions, state.peak_equity_usd, state.drawdown_usd,
            json.dumps({"category": state.category_exposure,
                        "strategy": state.strategy_exposure,
                        "consecutive_losses": state.consecutive_losses}),
        ),
    )


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #
def evaluate_portfolio(
    *,
    conn: sqlite3.Connection,
    settings: Settings,
    proposed_size_usd: float,
    category: str | None = None,
    strategy: str | None = None,
) -> GovernanceDecision:
    locks = [n for n, _ in active_locks(conn)]
    if locks:
        return GovernanceDecision(False, "locked", {"locks": locks}, engaged_locks=locks)

    state = compute_state(conn, settings)
    snapshot(conn, state)

    if state.drawdown_usd >= settings.max_portfolio_drawdown_usd:
        engage_lock(conn, LOCK_DRAWDOWN,
                    f"drawdown=${state.drawdown_usd:.2f} >= cap=${settings.max_portfolio_drawdown_usd:.2f}")
        return GovernanceDecision(False, "drawdown_breach",
                                  {"drawdown_usd": state.drawdown_usd})

    if state.consecutive_losses >= settings.max_consecutive_losses:
        engage_lock(conn, LOCK_CONSECUTIVE_LOSSES,
                    f"consecutive_losses={state.consecutive_losses}")
        return GovernanceDecision(False, "consecutive_losses",
                                  {"streak": state.consecutive_losses})

    new_open = state.open_exposure_usd + proposed_size_usd
    if new_open > settings.max_open_exposure_usd:
        return GovernanceDecision(False, "open_exposure_cap",
                                  {"projected": new_open,
                                   "cap": settings.max_open_exposure_usd})

    if category:
        new_cat = state.category_exposure.get(category, 0.0) + proposed_size_usd
        if new_cat > settings.max_category_exposure_usd:
            return GovernanceDecision(False, "category_exposure_cap",
                                      {"category": category, "projected": new_cat,
                                       "cap": settings.max_category_exposure_usd})

    if strategy:
        new_strat = state.strategy_exposure.get(strategy, 0.0) + proposed_size_usd
        if new_strat > settings.max_strategy_exposure_usd:
            return GovernanceDecision(False, "strategy_exposure_cap",
                                      {"strategy": strategy, "projected": new_strat,
                                       "cap": settings.max_strategy_exposure_usd})

    return GovernanceDecision(True)


# --------------------------------------------------------------------------- #
# Health-driven lock engagement
# --------------------------------------------------------------------------- #
def check_data_staleness(conn: sqlite3.Connection, settings: Settings) -> bool:
    """Engage stale_data lock if newest snapshot is older than the threshold."""
    row = conn.execute(
        "SELECT MAX(captured_at) FROM market_snapshots"
    ).fetchone()
    last = row[0]
    if not last:
        return False
    try:
        ts = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    age_min = (datetime.now(timezone.utc) - ts).total_seconds() / 60.0
    if age_min > settings.max_data_staleness_minutes:
        engage_lock(conn, LOCK_STALE_DATA,
                    f"newest snapshot is {age_min:.1f} min old")
        return True
    return False


def to_dict(state: PortfolioState) -> dict:
    return asdict(state)
