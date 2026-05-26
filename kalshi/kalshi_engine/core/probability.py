"""Probability math: implied prob, edge, EV, confidence band.

Kalshi prices are integer cents 1..99 representing the probability of YES
resolving. A YES contract pays $1 if YES, $0 otherwise — so the implied
probability of YES is simply `price / 100`.

All math here is intentionally pure and stateless so it can be unit-tested
without any network or DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Side = Literal["yes", "no"]

# Kalshi fee schedule (rounded): ~7% of `qty * price * (1 - price)` per side,
# clipped to one cent minimum, applied on buy and on settlement of a winner.
# Keeping a single conservative estimator here; refine per the docs once we
# wire live trading.
def estimate_fee_usd(qty: int, price_cents: int) -> float:
    if qty <= 0 or price_cents <= 0 or price_cents >= 100:
        return 0.0
    p = price_cents / 100.0
    fee = 0.07 * qty * p * (1 - p)
    return max(0.01 * qty, fee)


@dataclass(frozen=True)
class FairEstimate:
    fair_prob: float            # 0..1
    confidence: float           # 0..1 (subjective strength of evidence)
    uncertainty_lo: float       # 0..1
    uncertainty_hi: float       # 0..1


@dataclass(frozen=True)
class EdgeReport:
    side: Side
    implied_prob: float
    fair_prob: float
    edge: float                 # fair - implied for chosen side (>=0 means acting)
    expected_value: float       # $ per $1 risked, AFTER fees
    confidence: float
    uncertainty_lo: float
    uncertainty_hi: float
    overlaps_market: bool


def implied_yes_prob(yes_ask_cents: int | None) -> float | None:
    """Probability implied by *paying the ask* — the cost of getting in."""
    if yes_ask_cents is None or not (0 < yes_ask_cents < 100):
        return None
    return yes_ask_cents / 100.0


def implied_no_prob(no_ask_cents: int | None) -> float | None:
    if no_ask_cents is None or not (0 < no_ask_cents < 100):
        return None
    return no_ask_cents / 100.0


def midpoint_prob(yes_bid: int | None, yes_ask: int | None) -> float | None:
    if yes_bid is None or yes_ask is None:
        return None
    return ((yes_bid + yes_ask) / 2.0) / 100.0


def expected_value_per_dollar(fair_prob: float, price_cents: int, side: Side) -> float:
    """EV in dollars per $1 risked, after estimated fees.

    Buy YES at price p (cents): pay p/100, win 1 with prob fair_prob, else 0.
    Buy NO at price p (cents): pay p/100, win 1 with prob (1 - fair_prob).
    """
    if not (0 < price_cents < 100):
        return 0.0
    cost = price_cents / 100.0
    win_prob = fair_prob if side == "yes" else (1.0 - fair_prob)
    # one contract = $1 notional; fee per contract:
    fee_one = estimate_fee_usd(1, price_cents)
    gross = win_prob * 1.0 - cost
    return (gross - fee_one) / cost  # normalise to "per $ risked"


def build_edge_report(
    *,
    yes_bid: int | None,
    yes_ask: int | None,
    no_bid: int | None,
    no_ask: int | None,
    fair: FairEstimate,
) -> EdgeReport | None:
    """Pick the best side to act on, compute edge + EV against fees."""
    yes_p = implied_yes_prob(yes_ask)
    no_p = implied_no_prob(no_ask)

    candidates: list[tuple[Side, float, int]] = []
    if yes_p is not None and yes_ask is not None:
        candidates.append(("yes", fair.fair_prob - yes_p, yes_ask))
    if no_p is not None and no_ask is not None:
        candidates.append(("no", (1.0 - fair.fair_prob) - no_p, no_ask))

    if not candidates:
        return None

    side, edge, price = max(candidates, key=lambda x: x[1])
    if edge <= 0:
        # still emit a report — caller decides whether to log as rejected
        implied = price / 100.0 if side == "yes" else price / 100.0
        return EdgeReport(
            side=side,
            implied_prob=implied,
            fair_prob=fair.fair_prob if side == "yes" else 1 - fair.fair_prob,
            edge=edge,
            expected_value=expected_value_per_dollar(fair.fair_prob, price, side),
            confidence=fair.confidence,
            uncertainty_lo=fair.uncertainty_lo,
            uncertainty_hi=fair.uncertainty_hi,
            overlaps_market=_overlaps(fair, side, price),
        )

    ev = expected_value_per_dollar(fair.fair_prob, price, side)
    return EdgeReport(
        side=side,
        implied_prob=price / 100.0,
        fair_prob=fair.fair_prob if side == "yes" else 1 - fair.fair_prob,
        edge=edge,
        expected_value=ev,
        confidence=fair.confidence,
        uncertainty_lo=fair.uncertainty_lo,
        uncertainty_hi=fair.uncertainty_hi,
        overlaps_market=_overlaps(fair, side, price),
    )


def _overlaps(fair: FairEstimate, side: Side, price_cents: int) -> bool:
    """Does the uncertainty band straddle the market price?

    For YES side we compare to fair band directly. For NO side we flip the
    band to (1 - hi, 1 - lo) so the comparison stays in the same space as
    the NO price.
    """
    market = price_cents / 100.0
    if side == "yes":
        return fair.uncertainty_lo <= market <= fair.uncertainty_hi
    lo, hi = 1.0 - fair.uncertainty_hi, 1.0 - fair.uncertainty_lo
    return lo <= market <= hi


def kelly_fraction(fair_prob: float, price_cents: int, side: Side, cap: float = 0.25) -> float:
    """Capped Kelly — returns the fraction of bankroll to wager.

    For a binary bet at price p paying $1: b = (1-p)/p, q = 1-w.
    """
    p = price_cents / 100.0
    if not (0 < p < 1):
        return 0.0
    w = fair_prob if side == "yes" else 1 - fair_prob
    b = (1 - p) / p
    raw = (b * w - (1 - w)) / b
    return max(0.0, min(cap, raw))
