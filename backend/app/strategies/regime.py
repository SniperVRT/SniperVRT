"""Lightweight market-regime classifier — bull/bear/chop/high-vol."""
from __future__ import annotations

import pandas as pd

from .indicators import ema, realized_vol


def detect_regime(df: pd.DataFrame) -> dict:
    if df.empty or len(df) < 200:
        return {"regime": "unknown", "trend_score": 0.0, "vol_score": 0.0}
    fast = ema(df["close"], 50).iloc[-1]
    slow = ema(df["close"], 200).iloc[-1]
    last = float(df["close"].iloc[-1])
    rv = realized_vol(df["close"], 24)
    rv_rank = float(rv.rolling(200).rank(pct=True).iloc[-1]) if rv.notna().sum() > 0 else 0.5

    trend_score = (fast - slow) / slow if slow > 0 else 0.0
    if trend_score > 0.02 and rv_rank < 0.85:
        regime = "bull"
    elif trend_score < -0.02 and rv_rank < 0.85:
        regime = "bear"
    elif rv_rank >= 0.85:
        regime = "high_vol"
    else:
        regime = "chop"
    return {
        "regime": regime,
        "trend_score": round(float(trend_score), 5),
        "vol_score": round(float(rv_rank), 4),
        "last_price": last,
    }
