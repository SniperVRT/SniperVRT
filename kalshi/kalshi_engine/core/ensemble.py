"""Weighted strategy ensemble.

Each strategy returns a `FairEstimate(fair_prob, confidence, lo, hi)`.
The ensemble combines them into a single fair-prob estimate with an
honest agreement-discounted confidence, then computes edge + EV against
the current market.

Strategies do not trade — they vote. The risk engine remains the final
authority on whether the resulting recommendation can become an order.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Iterable

from ..db import utc_now_iso
from .probability import FairEstimate, build_edge_report, midpoint_prob


# Per-strategy priors. Tune from calibration history later — these are the
# pre-evidence weights before each estimate's own confidence is applied.
DEFAULT_PRIORS: dict[str, float] = {
    "news_lag": 1.0,
    "rules_mispricing": 1.2,    # spec calls out this as a strong source
    "mean_reversion": 0.8,
    "liquidity_dislocation": 0.7,
    "event_countdown": 0.6,
}


@dataclass
class StrategyVote:
    strategy: str
    fair: FairEstimate


@dataclass
class EnsembleResult:
    ticker: str
    votes: list[StrategyVote]
    combined_fair: float
    combined_confidence: float
    agreement: float           # 0..1
    implied_prob: float
    edge: float
    expected_value: float
    recommendation: str        # "yes" | "no" | "hold"
    side_price_cents: int | None
    band_lo: float
    band_hi: float
    flags: list[str] = field(default_factory=list)


def _weighted_mean(values: list[float], weights: list[float]) -> float:
    s = sum(weights)
    if s <= 0:
        return sum(values) / len(values) if values else 0.0
    return sum(v * w for v, w in zip(values, weights)) / s


def combine(
    *,
    ticker: str,
    votes: Iterable[StrategyVote],
    yes_bid: int | None,
    yes_ask: int | None,
    no_bid: int | None,
    no_ask: int | None,
    priors: dict[str, float] | None = None,
) -> EnsembleResult | None:
    votes = list(votes)
    if not votes:
        return None

    priors = priors or DEFAULT_PRIORS
    fair_values = [v.fair.fair_prob for v in votes]
    confidences = [v.fair.confidence for v in votes]
    weights = [priors.get(v.strategy, 0.5) * max(0.01, v.fair.confidence) for v in votes]

    combined_fair = _weighted_mean(fair_values, weights)

    # Agreement: 1 - normalised stdev across strategies (max stdev = 0.5)
    if len(fair_values) == 1:
        agreement = 1.0
    else:
        mean = sum(fair_values) / len(fair_values)
        var = sum((x - mean) ** 2 for x in fair_values) / len(fair_values)
        stdev = var ** 0.5
        agreement = max(0.0, 1.0 - stdev / 0.5)

    # Combined confidence: mean confidence × agreement, lightly boosted by
    # multiple independent signals agreeing.
    mean_conf = sum(confidences) / len(confidences)
    multi_boost = min(0.15, 0.05 * (len(votes) - 1))
    combined_conf = max(0.0, min(1.0, mean_conf * agreement + multi_boost * agreement))

    # Honest combined band: weighted union of input bands.
    band_lo = _weighted_mean([v.fair.uncertainty_lo for v in votes], weights)
    band_hi = _weighted_mean([v.fair.uncertainty_hi for v in votes], weights)
    if band_hi < band_lo:
        band_lo, band_hi = band_hi, band_lo

    combined_estimate = FairEstimate(
        fair_prob=combined_fair,
        confidence=combined_conf,
        uncertainty_lo=band_lo,
        uncertainty_hi=band_hi,
    )

    report = build_edge_report(
        yes_bid=yes_bid, yes_ask=yes_ask,
        no_bid=no_bid, no_ask=no_ask,
        fair=combined_estimate,
    )
    implied = midpoint_prob(yes_bid, yes_ask) or 0.0
    flags: list[str] = []
    if agreement < 0.7:
        flags.append("low_agreement")
    if len(votes) == 1:
        flags.append("single_strategy")

    if report is None:
        return EnsembleResult(
            ticker=ticker, votes=votes, combined_fair=combined_fair,
            combined_confidence=combined_conf, agreement=agreement,
            implied_prob=implied, edge=0.0, expected_value=0.0,
            recommendation="hold", side_price_cents=None,
            band_lo=band_lo, band_hi=band_hi, flags=flags + ["no_book"],
        )

    recommendation = "hold"
    if report.edge > 0 and not report.overlaps_market:
        recommendation = report.side
    if report.overlaps_market:
        flags.append("band_overlaps_market")

    price = yes_ask if report.side == "yes" else no_ask
    return EnsembleResult(
        ticker=ticker, votes=votes, combined_fair=combined_fair,
        combined_confidence=combined_conf, agreement=agreement,
        implied_prob=report.implied_prob, edge=report.edge,
        expected_value=report.expected_value,
        recommendation=recommendation,
        side_price_cents=price,
        band_lo=band_lo, band_hi=band_hi, flags=flags,
    )


def persist(conn: sqlite3.Connection, r: EnsembleResult) -> int:
    payload = [
        {
            "strategy": v.strategy,
            "fair": v.fair.fair_prob,
            "conf": v.fair.confidence,
            "lo": v.fair.uncertainty_lo,
            "hi": v.fair.uncertainty_hi,
        }
        for v in r.votes
    ]
    cur = conn.execute(
        """
        INSERT INTO ensemble_votes (
            ticker, votes_json, combined_fair, combined_confidence,
            agreement_score, implied_prob, edge, expected_value,
            recommendation, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            r.ticker, json.dumps(payload), r.combined_fair, r.combined_confidence,
            r.agreement, r.implied_prob, r.edge, r.expected_value,
            r.recommendation, utc_now_iso(),
        ),
    )
    return int(cur.lastrowid)
