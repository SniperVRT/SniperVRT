"""Weighted ensemble of strategies. Weights adapt slowly from recent rolling Sharpe."""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .base import Strategy
from .registry import STRATEGY_REGISTRY


class Ensemble(Strategy):
    name = "ensemble"
    family = "ensemble"
    default_params = {
        "members": ["ema_trend", "rsi_meanrev", "breakout", "vol_regime"],
        "weights": None,                # if None, equal-weight
        "vote_threshold": 0.35,         # min average score to take a side
        "adaptive": True,
        "adapt_lookback": 200,
    }

    def __init__(self, params: Optional[dict] = None) -> None:
        super().__init__(params)
        self._members = []
        for n in self.params["members"]:
            cls = STRATEGY_REGISTRY.get(n)
            if cls is None or cls is self.__class__:
                continue
            self._members.append(cls())

    def _weights(self, df: pd.DataFrame) -> list[float]:
        n = len(self._members)
        if not self.params.get("adaptive"):
            w = self.params.get("weights") or [1.0 / n] * n
            return w
        # Sharpe-like weight from recent rolling pnl of each member's signal
        lookback = self.params.get("adapt_lookback", 200)
        scores = []
        rets = np.log(df["close"] / df["close"].shift(1)).fillna(0.0)
        for m in self._members:
            try:
                out = m.generate(df).tail(lookback)
                sig = out["signal"].shift(1).fillna(0.0).reindex(rets.tail(lookback).index).fillna(0.0)
                pnl = sig * rets.tail(lookback)
                std = float(pnl.std())
                mean = float(pnl.mean())
                s = mean / std if std > 0 else 0.0
                scores.append(max(0.0, s))
            except Exception:
                scores.append(0.0)
        total = sum(scores)
        if total <= 0:
            return [1.0 / n] * n
        return [s / total for s in scores]

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        if not self._members:
            return pd.DataFrame({"signal": np.zeros(len(df))}, index=df.index)
        weights = self._weights(df)
        signal_sum = pd.Series(np.zeros(len(df)), index=df.index)
        conf_sum = pd.Series(np.zeros(len(df)), index=df.index)
        stops = pd.Series(np.zeros(len(df)), index=df.index)
        tps = pd.Series(np.zeros(len(df)), index=df.index)
        for m, w in zip(self._members, weights):
            o = m.generate(df)
            sig = o["signal"].astype(float).reindex(df.index).fillna(0.0)
            conf = o.get("confidence", pd.Series(0.5, index=df.index)).reindex(df.index).fillna(0.0)
            signal_sum = signal_sum + sig * conf * w
            conf_sum = conf_sum + conf * w
            stops = stops + o.get("stop_pct", pd.Series(0.02, index=df.index)).reindex(df.index).fillna(0.02) * w
            tps = tps + o.get("tp_pct", pd.Series(0.05, index=df.index)).reindex(df.index).fillna(0.05) * w
        thr = self.params["vote_threshold"]
        side = np.where(signal_sum > thr, 1, np.where(signal_sum < -thr, -1, 0))
        out = pd.DataFrame(index=df.index)
        out["signal"] = side
        out["confidence"] = conf_sum.clip(0, 1)
        out["stop_pct"] = stops
        out["tp_pct"] = tps
        out["reason"] = "ensemble_vote"
        return out
