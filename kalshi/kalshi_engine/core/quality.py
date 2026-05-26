"""Market quality scoring.

Combines spread, liquidity, depth, volume, freshness, tradability, and
rule clarity into a single 0..1 score plus per-component sub-scores so
downstream layers can explain *why* a market was kept or dropped.

Inputs are deliberately tolerant of missing fields — Kalshi sometimes
omits `volume_24h` or `open_interest` and we want a score regardless.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from ..connectors.kalshi import Market, Orderbook
from ..db import utc_now_iso


@dataclass(frozen=True)
class QualityScore:
    spread: float
    liquidity: float
    depth: float
    volume: float
    freshness: float
    tradability: float
    rules: float
    total: float
    flags: list[str]


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def _spread_score(spread_cents: int | None) -> float:
    if spread_cents is None:
        return 0.0
    # Linear: 0 cents = 1.0, 10+ cents = 0.0
    return _clip(1.0 - spread_cents / 10.0)


def _liquidity_score(liquidity: int | None) -> float:
    # Kalshi `liquidity` field roughly tracks total notional resting.
    if not liquidity or liquidity <= 0:
        return 0.0
    # 10k notional -> 1.0; log-shaped below that.
    import math
    return _clip(math.log10(liquidity + 1) / 4.0)


def _volume_score(vol24h: int | None) -> float:
    if not vol24h or vol24h <= 0:
        return 0.0
    import math
    return _clip(math.log10(vol24h + 1) / 4.0)


def _depth_score(book: Orderbook | None) -> float:
    if book is None:
        return 0.5  # neutral: we just didn't fetch it
    yes_qty = sum(q for _, q in book.yes[:3])
    no_qty = sum(q for _, q in book.no[:3])
    total = yes_qty + no_qty
    if total <= 0:
        return 0.0
    # 200+ contracts across top 3 levels each side -> 1.0
    return _clip(total / 400.0)


def _freshness_score(last_seen_at: str | None, now: datetime | None = None) -> float:
    if not last_seen_at:
        return 0.5
    try:
        ts = datetime.fromisoformat(last_seen_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.5
    now = now or datetime.now(timezone.utc)
    age_min = (now - ts).total_seconds() / 60.0
    if age_min <= 1:
        return 1.0
    if age_min >= 60:
        return 0.0
    return _clip(1.0 - age_min / 60.0)


def _tradability_score(m: Market) -> float:
    # 1) book present on both sides; 2) status active; 3) not at 1c/99c floor.
    score = 0.0
    if m.yes_bid is not None and m.yes_ask is not None: score += 0.3
    if m.no_bid is not None and m.no_ask is not None:   score += 0.3
    if m.status in ("active", "open"):                  score += 0.2
    if m.yes_ask is not None and 2 <= m.yes_ask <= 98:  score += 0.2
    return _clip(score)


def _rules_score(m: Market) -> float:
    rules = (m.rules_primary or "")
    if not rules:
        return 0.0
    base = 0.5
    rules_l = rules.lower()
    if m.settlement_source:                       base += 0.2
    if any(w in rules_l for w in ("official", "as reported by", "per the")):
        base += 0.15
    if any(w in rules_l for w in ("by 12:", "by 11:", "by 9:", "on or before", "no later than")):
        base += 0.15
    for vague in ("approximately", "around", "in the opinion of", "subjectively", "sometime"):
        if vague in rules_l:
            base -= 0.4
    return _clip(base)


def score_market(m: Market, *, book: Orderbook | None = None) -> QualityScore:
    spread = _spread_score(m.spread_cents)
    liquidity = _liquidity_score(m.liquidity)
    depth = _depth_score(book)
    volume = _volume_score(m.volume_24h)
    freshness = _freshness_score(utc_now_iso())  # captured now
    tradability = _tradability_score(m)
    rules = _rules_score(m)

    weights = {
        "spread": 0.20, "liquidity": 0.15, "depth": 0.10,
        "volume": 0.15, "freshness": 0.05, "tradability": 0.15, "rules": 0.20,
    }
    total = (
        weights["spread"] * spread
        + weights["liquidity"] * liquidity
        + weights["depth"] * depth
        + weights["volume"] * volume
        + weights["freshness"] * freshness
        + weights["tradability"] * tradability
        + weights["rules"] * rules
    )

    flags: list[str] = []
    if spread < 0.4: flags.append("wide_spread")
    if liquidity < 0.2: flags.append("thin_liquidity")
    if volume < 0.2: flags.append("low_volume")
    if rules < 0.4: flags.append("rules_unclear")
    if tradability < 0.7: flags.append("limited_tradability")

    return QualityScore(
        spread=spread, liquidity=liquidity, depth=depth, volume=volume,
        freshness=freshness, tradability=tradability, rules=rules,
        total=_clip(total), flags=flags,
    )


def persist(conn: sqlite3.Connection, ticker: str, score: QualityScore) -> None:
    conn.execute(
        """
        INSERT INTO market_quality (
            ticker, captured_at, spread_score, liquidity_score, depth_score,
            volume_score, freshness_score, tradability_score, rules_score,
            total_score, flags_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            ticker, utc_now_iso(), score.spread, score.liquidity, score.depth,
            score.volume, score.freshness, score.tradability, score.rules,
            score.total, json.dumps(score.flags),
        ),
    )


def to_dict(score: QualityScore) -> dict[str, Any]:
    return asdict(score)
