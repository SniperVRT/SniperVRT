"""Performance + calibration metrics beyond plain Brier.

Pulls from `probability_estimates` joined to `resolutions`, and from
`paper_positions` + `paper_pnl` for realised PnL stats.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass


@dataclass
class PerformanceReport:
    n: int
    brier: float
    log_loss: float
    avg_edge: float
    avg_realized: float
    profit_factor: float
    max_drawdown: float
    win_rate: float
    expectancy: float


def _safe_log(p: float) -> float:
    return math.log(max(1e-9, min(1 - 1e-9, p)))


def log_loss(samples: list[tuple[float, int]]) -> float:
    if not samples:
        return 0.0
    return -sum(o * _safe_log(p) + (1 - o) * _safe_log(1 - p) for p, o in samples) / len(samples)


def brier(samples: list[tuple[float, int]]) -> float:
    if not samples:
        return 0.0
    return sum((p - o) ** 2 for p, o in samples) / len(samples)


def _profit_factor(pnls: list[float]) -> float:
    gross_win = sum(x for x in pnls if x > 0)
    gross_loss = -sum(x for x in pnls if x < 0)
    if gross_loss <= 0:
        return float("inf") if gross_win > 0 else 0.0
    return gross_win / gross_loss


def _max_drawdown(pnls: list[float]) -> float:
    """Max drawdown on the cumulative PnL curve. Returns a non-negative number."""
    if not pnls:
        return 0.0
    peak = 0.0
    cum = 0.0
    mdd = 0.0
    for x in pnls:
        cum += x
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return mdd


def fetch_prediction_samples(
    conn: sqlite3.Connection,
    *,
    strategy: str | None = None,
) -> list[tuple[float, int]]:
    """[(predicted_yes_prob, realized_yes_bool), ...]."""
    if strategy:
        q = """
          SELECT pe.fair_prob,
                 CASE r.outcome WHEN 'yes' THEN 1 WHEN 'no' THEN 0 ELSE NULL END
            FROM probability_estimates pe
            JOIN resolutions r ON r.ticker = pe.ticker
           WHERE pe.strategy = ?
        """
        rows = conn.execute(q, (strategy,)).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT pe.fair_prob,
                   CASE r.outcome WHEN 'yes' THEN 1 WHEN 'no' THEN 0 ELSE NULL END
              FROM probability_estimates pe
              JOIN resolutions r ON r.ticker = pe.ticker
            """
        ).fetchall()
    return [(float(p), int(o)) for p, o in rows if o is not None]


def fetch_paper_pnls(
    conn: sqlite3.Connection,
    *,
    strategy: str | None = None,
) -> list[float]:
    if strategy:
        rows = conn.execute(
            "SELECT realized_pnl_usd FROM paper_positions "
            "WHERE realized_pnl_usd IS NOT NULL AND strategy=? "
            "ORDER BY closed_at ASC",
            (strategy,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT realized_pnl_usd FROM paper_positions "
            "WHERE realized_pnl_usd IS NOT NULL ORDER BY closed_at ASC",
        ).fetchall()
    return [float(r[0]) for r in rows]


def fetch_realized_edge_samples(
    conn: sqlite3.Connection,
    *,
    strategy: str | None = None,
) -> list[tuple[float, float]]:
    """[(predicted_edge, realized_signed_outcome), ...].

    realized_signed_outcome is +1 for a YES resolve, -1 for NO. Used to
    estimate whether predicted edge correlates with realised direction.
    """
    if strategy:
        q = """
          SELECT pe.edge,
                 CASE r.outcome WHEN 'yes' THEN 1.0 WHEN 'no' THEN -1.0 ELSE NULL END
            FROM probability_estimates pe
            JOIN resolutions r ON r.ticker = pe.ticker
           WHERE pe.strategy = ?
        """
        rows = conn.execute(q, (strategy,)).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT pe.edge,
                   CASE r.outcome WHEN 'yes' THEN 1.0 WHEN 'no' THEN -1.0 ELSE NULL END
              FROM probability_estimates pe
              JOIN resolutions r ON r.ticker = pe.ticker
            """
        ).fetchall()
    return [(float(e), float(o)) for e, o in rows if o is not None]


def performance_report(
    conn: sqlite3.Connection,
    *,
    strategy: str | None = None,
) -> PerformanceReport:
    samples = fetch_prediction_samples(conn, strategy=strategy)
    edge_samples = fetch_realized_edge_samples(conn, strategy=strategy)
    pnls = fetch_paper_pnls(conn, strategy=strategy)

    n = len(samples)
    b = brier(samples)
    ll = log_loss(samples)
    avg_edge = sum(e for e, _ in edge_samples) / len(edge_samples) if edge_samples else 0.0
    avg_realized = sum(o for _, o in edge_samples) / len(edge_samples) if edge_samples else 0.0
    pf = _profit_factor(pnls)
    mdd = _max_drawdown(pnls)
    wins = sum(1 for x in pnls if x > 0)
    win_rate = wins / len(pnls) if pnls else 0.0
    expectancy = sum(pnls) / len(pnls) if pnls else 0.0

    return PerformanceReport(
        n=n, brier=b, log_loss=ll,
        avg_edge=avg_edge, avg_realized=avg_realized,
        profit_factor=pf, max_drawdown=mdd,
        win_rate=win_rate, expectancy=expectancy,
    )
