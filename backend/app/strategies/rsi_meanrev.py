"""RSI mean-reversion: long oversold, short overbought, only in non-trending regimes."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy
from .indicators import rsi, ema, atr


class RsiMeanRev(Strategy):
    name = "rsi_meanrev"
    family = "mean_reversion"
    default_params = {
        "rsi_len": 14, "oversold": 28, "overbought": 72,
        "trend_len": 200, "atr_len": 14, "atr_stop_mult": 1.8, "atr_tp_mult": 2.5,
    }

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = pd.DataFrame(index=df.index)
        r = rsi(df["close"], p["rsi_len"])
        trend = ema(df["close"], p["trend_len"])
        atr_v = atr(df, p["atr_len"])

        # only mean-revert when price is near the long-term trend (not strongly trending)
        rel = (df["close"] - trend).abs() / df["close"]
        non_trend = rel < 0.05

        long_cond = (r < p["oversold"]) & non_trend
        short_cond = (r > p["overbought"]) & non_trend
        out["signal"] = np.where(long_cond, 1, np.where(short_cond, -1, 0))

        # confidence grows the further RSI is from the threshold
        dist_long = ((p["oversold"] - r) / p["oversold"]).clip(0, 1)
        dist_short = ((r - p["overbought"]) / (100 - p["overbought"])).clip(0, 1)
        out["confidence"] = np.where(out["signal"] > 0, dist_long,
                              np.where(out["signal"] < 0, dist_short, 0.0))
        out["stop_pct"] = (atr_v * p["atr_stop_mult"]) / df["close"]
        out["tp_pct"] = (atr_v * p["atr_tp_mult"]) / df["close"]
        out["reason"] = "rsi_meanrev_signal"
        return out
