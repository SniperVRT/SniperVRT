"""Alternative allocation methods: fractional Kelly, risk parity."""

from __future__ import annotations

import math
from typing import Iterable

from ..config import CopyTradeSettings
from ..ranking.score import TraderScore


def _stats(returns: list[float]) -> tuple[float, float]:
    if len(returns) < 2:
        return 0.0, 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return mean, math.sqrt(var)


def fractional_kelly(
    eligible: list[TraderScore],
    returns_by_uid: dict[str, list[float]],
    settings: CopyTradeSettings,
) -> dict[str, float]:
    total = settings.total_capital_usdt
    if not eligible:
        return {}
    raw: dict[str, float] = {}
    for s in eligible:
        mu, sigma = _stats(returns_by_uid.get(s.master_uid, []))
        if sigma < 1e-9 or mu <= 0:
            continue
        kelly = (mu / (sigma * sigma)) * settings.kelly_fraction
        raw[s.master_uid] = max(0.0, kelly)
    if not raw:
        return {}
    total_w = sum(raw.values())
    fracs = {u: w / total_w for u, w in raw.items()}
    clipped = {u: min(settings.max_allocation_pct,
                      max(settings.min_allocation_pct, f))
               for u, f in fracs.items()}
    s = sum(clipped.values())
    if s > 1.0:
        clipped = {u: f / s for u, f in clipped.items()}
    return {u: round(f * total, 2) for u, f in clipped.items()}


def risk_parity(
    eligible: list[TraderScore],
    returns_by_uid: dict[str, list[float]],
    settings: CopyTradeSettings,
) -> dict[str, float]:
    total = settings.total_capital_usdt
    if not eligible:
        return {}
    raw: dict[str, float] = {}
    for s in eligible:
        _, sigma = _stats(returns_by_uid.get(s.master_uid, []))
        if sigma < 1e-9:
            continue
        raw[s.master_uid] = 1.0 / sigma
    if not raw:
        return {}
    total_w = sum(raw.values())
    fracs = {u: w / total_w for u, w in raw.items()}
    clipped = {u: min(settings.max_allocation_pct,
                      max(settings.min_allocation_pct, f))
               for u, f in fracs.items()}
    s = sum(clipped.values())
    if s > 1.0:
        clipped = {u: f / s for u, f in clipped.items()}
    return {u: round(f * total, 2) for u, f in clipped.items()}
