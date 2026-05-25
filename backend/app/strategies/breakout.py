"""Donchian breakout after volatility compression."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy
from .indicators import atr, donchian, bollinger


class BreakoutCompression(Strategy):
    name = "breakout"
    family = "breakout"
    default_params = {
        "lookback": 20, "atr_len": 14,
        "compression_len": 50, "bb_len": 20, "bb_mult": 2.0,
        "atr_stop_mult": 2.0, "atr_tp_mult": 5.0,
    }

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = pd.DataFrame(index=df.index)
        lower, upper = donchian(df, p["lookback"])
        atr_v = atr(df, p["atr_len"])
        bl, _, bu = bollinger(df["close"], p["bb_len"], p["bb_mult"])
        bb_width = (bu - bl) / df["close"]
        comp = bb_width.rolling(p["compression_len"]).rank(pct=True)
        compressed = comp < 0.25  # bottom quartile = compressed

        prev_upper = upper.shift(1)
        prev_lower = lower.shift(1)
        long_cond = (df["close"] > prev_upper) & compressed
        short_cond = (df["close"] < prev_lower) & compressed
        out["signal"] = np.where(long_cond, 1, np.where(short_cond, -1, 0))
        out["confidence"] = (1.0 - comp).clip(0, 1).fillna(0)
        out["stop_pct"] = (atr_v * p["atr_stop_mult"]) / df["close"]
        out["tp_pct"] = (atr_v * p["atr_tp_mult"]) / df["close"]
        out["reason"] = "breakout_compression"
        return out
