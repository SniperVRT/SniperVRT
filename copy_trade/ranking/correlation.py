"""Pairwise vault return correlation for portfolio dedup.

Research finding (from arXiv meta-CTA): two vaults both trading BTC longs
are NOT diversification. If correlation between vaults A and B exceeds
threshold (default 0.70 on daily returns), drop the lower-scored one.

Correlation is computed once per leaderboard poll on the equity-curve
returns from each vault's `accountValueHistory` and cached in the
`vault_correlations` table.
"""

from __future__ import annotations

import math
import sqlite3
from typing import Iterable

import structlog

from ..db import utc_now_iso

log = structlog.get_logger("copy_trade.correlation")

DEFAULT_CORR_THRESHOLD = 0.70


def returns_from_equity_curve(history: list) -> list[float]:
    """Convert accountValueHistory → list of period-over-period returns."""
    if not history or len(history) < 2:
        return []
    try:
        values = [float(p[1]) for p in history]
    except (TypeError, ValueError, IndexError):
        return []
    out: list[float] = []
    for i in range(1, len(values)):
        prev = values[i - 1]
        if prev > 0:
            out.append((values[i] - prev) / prev)
    return out


def pearson_correlation(a: list[float], b: list[float]) -> float | None:
    """Pearson correlation on aligned series. Returns None if too short."""
    n = min(len(a), len(b))
    if n < 5:
        return None
    a, b = a[-n:], b[-n:]
    mean_a, mean_b = sum(a) / n, sum(b) / n
    num = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((x - mean_b) ** 2 for x in b)
    denom = math.sqrt(var_a * var_b)
    if denom < 1e-12:
        return None
    return num / denom


def persist_correlation(conn: sqlite3.Connection, a: str, b: str,
                        corr: float, n: int) -> None:
    lo, hi = sorted((a, b))
    conn.execute(
        "INSERT INTO vault_correlations (vault_a, vault_b, correlation, "
        "n_points, computed_at) VALUES (?,?,?,?,?)",
        (lo, hi, corr, n, utc_now_iso()),
    )


def dedup_correlated(
    candidates: list,
    returns_by_uid: dict[str, list[float]],
    threshold: float = DEFAULT_CORR_THRESHOLD,
    conn: sqlite3.Connection | None = None,
) -> list:
    """Greedy correlation dedup.

    Iterate `candidates` (sorted best→worst by composite score). For each
    candidate, drop it if it correlates above `threshold` with any already-
    accepted candidate.

    `returns_by_uid` maps master_uid → list of returns (e.g. daily).
    Pass `conn` to persist the correlations into `vault_correlations`.
    """
    kept: list = []
    for c in candidates:
        uid = getattr(c, "master_uid", None) or c.get("master_uid")
        my_returns = returns_by_uid.get(uid) or []
        drop = False
        for k in kept:
            other_uid = getattr(k, "master_uid", None) or k.get("master_uid")
            corr = pearson_correlation(my_returns, returns_by_uid.get(other_uid) or [])
            if corr is None:
                continue
            if conn is not None:
                persist_correlation(conn, uid, other_uid, corr,
                                    min(len(my_returns),
                                        len(returns_by_uid.get(other_uid) or [])))
            if corr >= threshold:
                drop = True
                log.info("dedup_dropped", drop=uid, against=other_uid, corr=corr)
                break
        if not drop:
            kept.append(c)
    return kept
