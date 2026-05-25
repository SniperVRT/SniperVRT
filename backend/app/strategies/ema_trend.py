"""EMA trend-following: long when fast EMA > slow EMA and slope is up."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy
from .indicators import ema, atr


class EmaTrend(Strategy):
    name = "ema_trend"
    family = "trend"
    default_params = {"fast": 12, "slow": 34, "atr_len": 14, "atr_stop_mult": 2.5, "atr_tp_mult": 4.0}

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = pd.DataFrame(index=df.index)
        fast = ema(df["close"], p["fast"])
        slow = ema(df["close"], p["slow"])
        slope = fast.diff(3)
        atr_v = atr(df, p["atr_len"])

        long_cond = (fast > slow) & (slope > 0)
        short_cond = (fast < slow) & (slope < 0)
        out["signal"] = np.where(long_cond, 1, np.where(short_cond, -1, 0))

        spread = (fast - slow).abs() / df["close"].replace(0, np.nan)
        conf = (spread.rolling(20).rank(pct=True)).fillna(0.5)
        out["confidence"] = conf.clip(0.0, 1.0)
        out["stop_pct"] = (atr_v * p["atr_stop_mult"]) / df["close"]
        out["tp_pct"] = (atr_v * p["atr_tp_mult"]) / df["close"]
        out["reason"] = "ema_trend_signal"
        return out
