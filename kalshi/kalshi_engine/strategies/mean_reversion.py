"""Overreaction mean reversion: the market moved too far on emotion / a
single headline. Pull the price back toward a recent stable baseline,
but only if the move size and recency justify it.

The estimate uses recent snapshots stored locally — no external evidence
required, just a price history baseline.
"""

from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..connectors.kalshi import Market
from ..core.probability import FairEstimate, midpoint_prob


@dataclass
class OverreactionMeanReversionStrategy:
    name: str = "mean_reversion"
    baseline_lookback_minutes: int = 240   # 4h
    min_move_cents: int = 8                # smaller moves are just noise
    revert_fraction: float = 0.4           # pull back 40% of the move

    def estimate(
        self,
        market: Market,
        evidence: dict[str, Any] | None = None,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> FairEstimate | None:
        if conn is None:
            return None
        mid = midpoint_prob(market.yes_bid, market.yes_ask)
        if mid is None:
            return None

        since = (datetime.now(timezone.utc) - timedelta(minutes=self.baseline_lookback_minutes)).isoformat()
        rows = conn.execute(
            """
            SELECT yes_bid, yes_ask FROM market_snapshots
             WHERE ticker = ? AND captured_at >= ?
               AND yes_bid IS NOT NULL AND yes_ask IS NOT NULL
             ORDER BY captured_at ASC
            """,
            (market.ticker, since),
        ).fetchall()
        if len(rows) < 8:
            return None

        midpoints = [(r[0] + r[1]) / 2.0 for r in rows]
        baseline_cents = statistics.median(midpoints[: max(3, len(midpoints) // 3)])
        current_cents = (market.yes_bid + market.yes_ask) / 2.0
        move_cents = current_cents - baseline_cents
        if abs(move_cents) < self.min_move_cents:
            return None

        target_cents = current_cents - move_cents * self.revert_fraction
        fair = max(0.01, min(0.99, target_cents / 100.0))

        # Confidence higher when the move is larger AND the baseline has low
        # variance (a stable pre-move price).
        try:
            stdev = statistics.pstdev(midpoints[: max(3, len(midpoints) // 3)])
        except statistics.StatisticsError:
            stdev = 1.0
        stability = max(0.0, 1.0 - stdev / 20.0)
        move_strength = min(1.0, abs(move_cents) / 30.0)
        conf = max(0.0, min(1.0, 0.5 * stability + 0.5 * move_strength))
        half = max(0.03, 0.18 * (1 - conf))
        return FairEstimate(
            fair_prob=fair,
            confidence=conf,
            uncertainty_lo=max(0.0, fair - half),
            uncertainty_hi=min(1.0, fair + half),
        )
