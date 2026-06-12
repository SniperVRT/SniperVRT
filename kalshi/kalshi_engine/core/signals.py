"""Signal builder + ranker.

A `Signal` is the engine's recommended action for a single market: which
side, the max price to pay, suggested size, the thesis, and the evidence
that produced the fair-prob estimate. Signals are *not* orders — execution
is gated by manual approval until we flip out of `signal` mode.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..connectors.kalshi import Market
from .probability import EdgeReport, FairEstimate, build_edge_report, kelly_fraction


@dataclass
class Signal:
    ticker: str
    side: str                    # "yes" | "no"
    strategy: str
    fair_prob: float
    implied_prob: float
    edge: float
    expected_value: float
    confidence: float
    max_price_cents: int
    suggested_size_usd: float
    thesis: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class Rejection:
    ticker: str
    strategy: str
    reason: str
    detail: dict[str, Any]


def _suggested_size(fair: FairEstimate, side: str, price_cents: int, settings: Settings) -> float:
    """Cap by both Kelly and max_position_usd; never exceed bankroll cap."""
    kelly = kelly_fraction(fair.fair_prob, price_cents, side, cap=0.10)  # capped at 10%
    by_kelly = settings.bankroll_usd * kelly
    return round(min(by_kelly, settings.max_position_usd), 2)


def build_signal(
    *,
    market: Market,
    fair: FairEstimate,
    strategy: str,
    settings: Settings,
    evidence: dict[str, Any] | None = None,
) -> Signal | Rejection:
    report = build_edge_report(
        yes_bid=market.yes_bid,
        yes_ask=market.yes_ask,
        no_bid=market.no_bid,
        no_ask=market.no_ask,
        fair=fair,
    )
    if report is None:
        return Rejection(market.ticker, strategy, "no_book", {})

    if report.edge < settings.min_edge:
        return Rejection(
            market.ticker,
            strategy,
            "edge_too_small",
            {"edge": report.edge, "min_edge": settings.min_edge},
        )
    if report.confidence < settings.min_confidence:
        return Rejection(
            market.ticker,
            strategy,
            "low_confidence",
            {"confidence": report.confidence, "min": settings.min_confidence},
        )
    if report.overlaps_market:
        return Rejection(
            market.ticker,
            strategy,
            "uncertainty_overlaps_market",
            {"lo": report.uncertainty_lo, "hi": report.uncertainty_hi},
        )
    if report.expected_value <= 0:
        return Rejection(
            market.ticker,
            strategy,
            "ev_after_fees_nonpositive",
            {"ev_per_dollar": report.expected_value},
        )

    price = market.yes_ask if report.side == "yes" else market.no_ask
    assert price is not None  # build_edge_report already gated this
    size = _suggested_size(fair, report.side, price, settings)
    if size <= 0:
        return Rejection(market.ticker, strategy, "size_zero", {"price_cents": price})

    thesis = (
        f"[{strategy}] fair={fair.fair_prob:.0%} vs implied={report.implied_prob:.0%} "
        f"-> edge={report.edge:+.1%}, EV/$={report.expected_value:+.2f}, "
        f"conf={fair.confidence:.2f}, band=[{fair.uncertainty_lo:.0%},{fair.uncertainty_hi:.0%}]"
    )
    return Signal(
        ticker=market.ticker,
        side=report.side,
        strategy=strategy,
        fair_prob=fair.fair_prob,
        implied_prob=report.implied_prob,
        edge=report.edge,
        expected_value=report.expected_value,
        confidence=report.confidence,
        max_price_cents=price,
        suggested_size_usd=size,
        thesis=thesis,
        evidence=evidence or {},
    )


def rank(signals: list[Signal]) -> list[Signal]:
    """Sort by edge × confidence × EV. Highest first."""
    return sorted(
        signals,
        key=lambda s: s.edge * s.confidence * max(s.expected_value, 0.0),
        reverse=True,
    )


def signal_to_row(s: Signal) -> dict[str, Any]:
    return {
        "ticker": s.ticker,
        "side": s.side,
        "strategy": s.strategy,
        "fair_prob": s.fair_prob,
        "implied_prob": s.implied_prob,
        "edge": s.edge,
        "expected_value": s.expected_value,
        "confidence": s.confidence,
        "max_price_cents": s.max_price_cents,
        "suggested_size_usd": s.suggested_size_usd,
        "thesis": s.thesis,
        "evidence_json": json.dumps(s.evidence),
    }
