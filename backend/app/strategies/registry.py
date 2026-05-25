"""Strategy registry — discoverable by name. Ensemble references registry, so we register it last."""
from __future__ import annotations

from typing import Type

from .base import Strategy
from .ema_trend import EmaTrend
from .rsi_meanrev import RsiMeanRev
from .breakout import BreakoutCompression
from .vol_regime import VolRegime


STRATEGY_REGISTRY: dict[str, Type[Strategy]] = {
    EmaTrend.name: EmaTrend,
    RsiMeanRev.name: RsiMeanRev,
    BreakoutCompression.name: BreakoutCompression,
    VolRegime.name: VolRegime,
}


def _late_register_ensemble() -> None:
    from .ensemble import Ensemble
    STRATEGY_REGISTRY[Ensemble.name] = Ensemble


_late_register_ensemble()


def build_strategy(name: str, params: dict | None = None) -> Strategy:
    cls = STRATEGY_REGISTRY.get(name)
    if cls is None:
        raise KeyError(f"unknown strategy: {name}")
    return cls(params=params)


def list_strategies() -> list[dict]:
    out = []
    for name, cls in STRATEGY_REGISTRY.items():
        inst = cls()
        out.append({
            "name": name,
            "family": inst.family,
            "default_params": inst.default_params,
        })
    return out
