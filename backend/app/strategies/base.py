"""Strategy interface."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


@dataclass
class Signal:
    side: str                    # "long" | "flat" | "short"
    confidence: float            # 0..1
    stop_pct: Optional[float] = None    # stop distance relative to entry
    take_profit_pct: Optional[float] = None
    reason: str = ""
    meta: dict = field(default_factory=dict)


class Strategy:
    """Strategies operate on a DataFrame of OHLCV indexed by datetime, columns: open, high, low, close, volume.

    Subclasses implement `generate(df)` -> pd.DataFrame with at least a "signal" column (+1/-1/0)
    and `signal_now(df)` -> Signal for live/paper decisions.
    """

    name: str = "base"
    family: str = "base"
    default_params: dict = {}

    def __init__(self, params: Optional[dict] = None) -> None:
        self.params = {**self.default_params, **(params or {})}

    def describe(self) -> dict:
        return {
            "name": self.name,
            "family": self.family,
            "params": dict(self.params),
        }

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError

    def signal_now(self, df: pd.DataFrame) -> Signal:
        out = self.generate(df)
        if out.empty:
            return Signal("flat", 0.0, reason="no data")
        row = out.iloc[-1]
        side = "long" if row["signal"] > 0 else ("short" if row["signal"] < 0 else "flat")
        conf = float(row.get("confidence", abs(row["signal"])))
        return Signal(
            side=side,
            confidence=conf,
            stop_pct=float(row["stop_pct"]) if "stop_pct" in row and pd.notna(row["stop_pct"]) else None,
            take_profit_pct=float(row["tp_pct"]) if "tp_pct" in row and pd.notna(row["tp_pct"]) else None,
            reason=str(row.get("reason", self.name)),
        )
