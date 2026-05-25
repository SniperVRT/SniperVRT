"""Realistic execution simulation: spread, slippage, fees, partial fill probability."""
from __future__ import annotations

from dataclasses import dataclass

from backend.app.core.config import get_config


@dataclass
class FillResult:
    fill_price: float
    requested_price: float
    fee: float
    slippage_cost: float
    quality_score: float       # 0..1; 1 means a perfect fill at requested price


def simulate_fill(side: str, requested_price: float, size_base: float,
                  taker: bool = True) -> FillResult:
    """Simulate the price you actually trade at after spread + slippage, and the fee paid."""
    cfg = get_config().execution
    spread = requested_price * (cfg.spread_bps / 10_000.0) / 2.0
    slip = requested_price * (cfg.slippage_bps / 10_000.0)
    if side == "buy":
        fill = requested_price + spread + slip
    else:
        fill = requested_price - spread - slip
    notional = abs(fill * size_base)
    fee_rate = cfg.taker_fee if taker else cfg.maker_fee
    fee = notional * fee_rate
    slippage_cost = abs(fill - requested_price) * abs(size_base)
    quality = 1.0 - min(1.0, (slippage_cost + spread * size_base) / max(1e-9, notional))
    return FillResult(
        fill_price=float(fill),
        requested_price=float(requested_price),
        fee=float(fee),
        slippage_cost=float(slippage_cost),
        quality_score=round(float(quality), 4),
    )
