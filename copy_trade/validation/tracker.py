"""PnL tracking and scoring validation.

Validates whether our scoring system actually predicts future performance.
Key question: do traders we scored high at time T outperform low-scored
traders over the subsequent 30 days?

Also maintains the running portfolio state snapshot for the safety gate.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

import structlog

from ..db import utc_now_iso

log = structlog.get_logger("copy_trade.tracker")


def update_portfolio_snapshot(conn: sqlite3.Connection,
                               total_capital: float) -> dict[str, Any]:
    """Aggregate all subscription PnL and write a portfolio snapshot."""
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(sp.realized_pnl), 0) AS total_realized,
            COALESCE(SUM(sp.unrealized_pnl), 0) AS total_unrealized,
            COALESCE(SUM(s.allocated_usdt), 0) AS deployed,
            COUNT(*) AS n_active
        FROM subscriptions s
        LEFT JOIN (
            SELECT subscription_id, realized_pnl, unrealized_pnl
            FROM subscription_pnl sp2
            WHERE sp2.id = (
                SELECT MAX(id) FROM subscription_pnl
                WHERE subscription_id = sp2.subscription_id
            )
        ) sp ON sp.subscription_id = s.id
        WHERE s.status = 'active'
        """
    ).fetchone()

    realized = float(row["total_realized"] or 0)
    unrealized = float(row["total_unrealized"] or 0)
    deployed = float(row["deployed"] or 0)
    n_active = int(row["n_active"] or 0)
    current_equity = total_capital + realized + unrealized

    # Peak equity from history
    peak_row = conn.execute(
        "SELECT MAX(total_capital + realized_pnl + unrealized_pnl) AS peak "
        "FROM portfolio_snapshots"
    ).fetchone()
    peak = float(peak_row["peak"] or current_equity)
    peak = max(peak, current_equity)
    drawdown_pct = (peak - current_equity) / peak if peak > 0 else 0.0

    conn.execute(
        """
        INSERT INTO portfolio_snapshots
            (captured_at, total_capital, deployed_usdt, realized_pnl,
             unrealized_pnl, peak_equity, drawdown_pct, active_masters)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            utc_now_iso(), total_capital, deployed,
            realized, unrealized, peak, drawdown_pct, n_active,
        ),
    )
    return {
        "equity": current_equity,
        "realized": realized,
        "unrealized": unrealized,
        "deployed": deployed,
        "drawdown_pct": drawdown_pct,
        "active_masters": n_active,
    }


def score_prediction_accuracy(conn: sqlite3.Connection,
                               lookback_days: int = 30) -> dict[str, Any]:
    """Compare past score rankings to actual PnL achieved.

    Returns correlation stats: did high-scored traders actually outperform?
    """
    cutoff = datetime.now(timezone.utc)
    rows = conn.execute(
        """
        SELECT ts.master_uid, ts.composite_score,
               COALESCE(SUM(sp.total_pnl), 0) AS realised_pnl
        FROM trader_scores ts
        JOIN subscriptions sub ON sub.master_uid = ts.master_uid
        JOIN subscription_pnl sp ON sp.subscription_id = sub.id
        WHERE ts.scored_at >= datetime(?, '-' || ? || ' days')
          AND sp.captured_at >= datetime(?, '-' || ? || ' days')
          AND ts.eligible = 1
        GROUP BY ts.master_uid, ts.composite_score
        """,
        (cutoff.isoformat(), lookback_days, cutoff.isoformat(), lookback_days),
    ).fetchall()

    if len(rows) < 3:
        return {"n": len(rows), "rank_correlation": None,
                "note": "insufficient_data"}

    scores = [float(r["composite_score"]) for r in rows]
    pnls = [float(r["realised_pnl"]) for r in rows]
    corr = _spearman_rank_correlation(scores, pnls)
    return {
        "n": len(rows),
        "rank_correlation": corr,
        "avg_pnl": sum(pnls) / len(pnls) if pnls else 0,
        "note": "positive_corr_means_scoring_predicts_pnl",
    }


def _spearman_rank_correlation(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 2:
        return 0.0
    rx = _ranks(x)
    ry = _ranks(y)
    d2 = sum((rx[i] - ry[i]) ** 2 for i in range(n))
    return 1.0 - (6.0 * d2) / (n * (n ** 2 - 1))


def _ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda t: t[1])
    ranks = [0.0] * len(values)
    for rank, (i, _) in enumerate(indexed, 1):
        ranks[i] = float(rank)
    return ranks
