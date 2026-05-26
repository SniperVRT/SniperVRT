"""Calibration & performance metrics computed off `probability_estimates`
joined to `resolutions`.

Profitability isn't enough — we want to know whether markets the engine
called "60%" actually resolve near 60% over a meaningful sample.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class CalibrationBucket:
    bucket_lo: float
    bucket_hi: float
    n: int
    avg_predicted: float
    realized_rate: float


@dataclass
class CalibrationReport:
    strategy: str
    n: int
    brier: float
    buckets: list[CalibrationBucket]


def _fetch(conn: sqlite3.Connection, strategy: str | None) -> list[tuple[float, int]]:
    """Return [(predicted_yes_prob, realized_yes_bool), ...]."""
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
        q = """
          SELECT pe.fair_prob,
                 CASE r.outcome WHEN 'yes' THEN 1 WHEN 'no' THEN 0 ELSE NULL END
            FROM probability_estimates pe
            JOIN resolutions r ON r.ticker = pe.ticker
        """
        rows = conn.execute(q).fetchall()
    return [(float(p), int(o)) for p, o in rows if o is not None]


def brier_score(samples: list[tuple[float, int]]) -> float:
    if not samples:
        return 0.0
    return sum((p - o) ** 2 for p, o in samples) / len(samples)


def calibration_report(
    conn: sqlite3.Connection,
    *,
    strategy: str | None = None,
    n_buckets: int = 10,
) -> CalibrationReport:
    samples = _fetch(conn, strategy)
    n = len(samples)
    buckets: list[CalibrationBucket] = []
    for i in range(n_buckets):
        lo, hi = i / n_buckets, (i + 1) / n_buckets
        in_b = [(p, o) for p, o in samples if lo <= p < hi or (hi == 1.0 and p == 1.0)]
        if not in_b:
            continue
        avg_p = sum(p for p, _ in in_b) / len(in_b)
        rate = sum(o for _, o in in_b) / len(in_b)
        buckets.append(CalibrationBucket(lo, hi, len(in_b), avg_p, rate))
    return CalibrationReport(
        strategy=strategy or "ALL",
        n=n,
        brier=brier_score(samples),
        buckets=buckets,
    )
