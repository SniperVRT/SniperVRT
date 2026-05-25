"""Volatility-regime: long only when vol is in a healthy band (not extreme)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy
from .indicators import ema, realized_vol, atr


class VolRegime(Strategy):
    name = "vol_regime"
    family = "regime"
    default_params = {
        "vol_len": 24, "vol_low_pct": 0.30, "vol_high_pct": 0.85,
        "trend_len": 100, "atr_len": 14, "atr_stop_mult": 2.0, "atr_tp_mult": 3.0,
    }

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = pd.DataFrame(index=df.index)
        rv = realized_vol(df["close"], p["vol_len"])
        rv_rank = rv.rolling(200).rank(pct=True)
        healthy = (rv_rank > p["vol_low_pct"]) & (rv_rank < p["vol_high_pct"])
        trend = ema(df["close"], p["trend_len"])
        atr_v = atr(df, p["atr_len"])

        long_cond = healthy & (df["close"] > trend)
        short_cond = (~healthy) & (df["close"] < trend) & (rv_rank > p["vol_high_pct"])
        out["signal"] = np.where(long_cond, 1, np.where(short_cond, -1, 0))
        conf = (1.0 - (rv_rank - 0.5).abs() * 2).clip(0, 1).fillna(0)
        out["confidence"] = conf
        out["stop_pct"] = (atr_v * p["atr_stop_mult"]) / df["close"]
        out["tp_pct"] = (atr_v * p["atr_tp_mult"]) / df["close"]
        out["reason"] = "vol_regime_signal"
        return out
