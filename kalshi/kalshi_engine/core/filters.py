"""Pre-trade filters. Reject anything illiquid, too wide, too vague, too
close to expiry, or too far out.

Each filter returns `(passed, reason_if_failed)` so the scanner can log
rejection reasons in `rejected_signals`.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from ..connectors.kalshi import Market

# Categories with notoriously messy rules or that the spec explicitly excludes
# from the first version (sports, vague politics, celebrity).
_BLOCKED_CATEGORY_KEYWORDS = (
    "celebrity",
    "entertainment",
    "sports",
)

# Subtitle / rules keywords that indicate the contract is too subjective
_VAGUE_KEYWORDS = (
    "in the opinion of",
    "subjectively",
    "approximately",
    "around",
    "sometime",
)


@dataclass(frozen=True)
class FilterResult:
    passed: bool
    reason: str | None = None
    detail: dict | None = None

    @classmethod
    def ok(cls) -> "FilterResult":
        return cls(True)

    @classmethod
    def fail(cls, reason: str, **detail) -> "FilterResult":
        return cls(False, reason, detail or None)


def check_status(m: Market) -> FilterResult:
    if m.status != "active" and m.status != "open":
        return FilterResult.fail("market_not_active", status=m.status)
    return FilterResult.ok()


def check_book_present(m: Market) -> FilterResult:
    if m.yes_bid is None or m.yes_ask is None or m.no_bid is None or m.no_ask is None:
        return FilterResult.fail("missing_book")
    return FilterResult.ok()


def check_spread(m: Market, settings: Settings) -> FilterResult:
    spread = m.spread_cents
    if spread is None:
        return FilterResult.fail("missing_book")
    if spread > settings.max_spread_cents:
        return FilterResult.fail("spread_too_wide", spread_cents=spread)
    return FilterResult.ok()


def check_liquidity(m: Market, settings: Settings) -> FilterResult:
    vol = m.volume_24h or 0
    if vol < settings.min_volume_24h:
        return FilterResult.fail("low_volume_24h", volume_24h=vol)
    return FilterResult.ok()


def check_time_to_close(m: Market, settings: Settings) -> FilterResult:
    mtc = m.minutes_to_close()
    if mtc is None:
        return FilterResult.fail("no_close_time")
    if mtc < settings.min_minutes_to_close:
        return FilterResult.fail("too_close_to_expiry", minutes_to_close=mtc)
    if mtc > settings.max_minutes_to_close:
        return FilterResult.fail("too_far_to_expiry", minutes_to_close=mtc)
    return FilterResult.ok()


def check_category(m: Market) -> FilterResult:
    cat = (m.category or "").lower()
    for kw in _BLOCKED_CATEGORY_KEYWORDS:
        if kw in cat:
            return FilterResult.fail("blocked_category", category=m.category)
    return FilterResult.ok()


def check_rules_clarity(m: Market) -> FilterResult:
    rules = (m.rules_primary or "").lower()
    if not rules:
        return FilterResult.fail("rules_missing")
    for kw in _VAGUE_KEYWORDS:
        if kw in rules:
            return FilterResult.fail("rules_vague", keyword=kw)
    if not m.settlement_source:
        # Some markets put the source in the rules text — soft-warn only.
        pass
    return FilterResult.ok()


_PIPELINE = (
    check_status,
    check_book_present,
    check_spread,
    check_liquidity,
    check_time_to_close,
    check_category,
    check_rules_clarity,
)


def evaluate(m: Market, settings: Settings) -> FilterResult:
    """Run the full pipeline. Returns the first failure or `ok()`."""
    for fn in _PIPELINE:
        try:
            res = fn(m, settings) if fn.__code__.co_argcount == 2 else fn(m)
        except TypeError:
            res = fn(m)
        if not res.passed:
            return res
    return FilterResult.ok()
