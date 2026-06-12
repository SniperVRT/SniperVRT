"""News-lag mispricing: the official source has already changed, the market
has not caught up yet.

Week-1 implementation is intentionally a stub: it consumes any externally
supplied `news_evidence` (e.g. a fetched headline or a data-feed reading)
attached to a market by the scanner's information layer. Week 2 wires the
real ingestion (RSS, govt data, etc.); the interface here stays the same.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..connectors.kalshi import Market
from ..core.probability import FairEstimate, midpoint_prob


@dataclass
class NewsLagStrategy:
    name: str = "news_lag"

    def estimate(self, market: Market, evidence: dict[str, Any] | None = None) -> FairEstimate | None:
        if not evidence or "news_signal" not in evidence:
            return None
        sig = evidence["news_signal"]
        # `sig` shape: {"direction": "yes"|"no", "strength": 0..1, "implied_shift": 0..1}
        direction = sig.get("direction")
        strength = float(sig.get("strength", 0.0))
        shift = float(sig.get("implied_shift", 0.0))
        if direction not in ("yes", "no") or strength <= 0 or shift <= 0:
            return None

        mid = midpoint_prob(market.yes_bid, market.yes_ask)
        if mid is None:
            return None

        delta = shift if direction == "yes" else -shift
        fair = max(0.01, min(0.99, mid + delta))
        # Confidence scales with evidence strength; uncertainty band shrinks
        # as confidence grows.
        conf = max(0.0, min(1.0, strength))
        half = max(0.02, 0.15 * (1 - conf))
        return FairEstimate(
            fair_prob=fair,
            confidence=conf,
            uncertainty_lo=max(0.0, fair - half),
            uncertainty_hi=min(1.0, fair + half),
        )
