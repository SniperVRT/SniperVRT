"""Upgraded regime detector with 8 nuanced states + confidence + features.

States:
  trend_up, trend_down, range_chop, compression, expansion,
  panic_down, euphoric_up, post_shock

Decision is rule-based over: EMA slope, EMA ratio, ATR%, BB width percentile,
volume z-score, 1-bar return magnitude. Each snapshot is persisted to
RegimeSnapshot for later correlation analysis.
"""
from __future__ import annotations

import time
from typing import Optional

import numpy as np
import pandas as pd

from backend.app.core.db import session_scope
from backend.app.data import load_candles
from backend.app.models import RegimeSnapshot
from backend.app.strategies.indicators import atr, bollinger, ema, realized_vol, zscore


def _features(df: pd.DataFrame) -> dict:
    closes = df["close"]
    e_fast = ema(closes, 50)
    e_slow = ema(closes, 200)
    atr_v = atr(df, 14)
    bb_l, _, bb_u = bollinger(closes, 20, 2.0)
    bb_w = (bb_u - bb_l) / closes
    rv = realized_vol(closes, 24)

    last = float(closes.iloc[-1])
    last_ret = float(np.log(closes.iloc[-1] / closes.iloc[-2])) if len(closes) >= 2 else 0.0
    trend_score = float((e_fast.iloc[-1] - e_slow.iloc[-1]) / e_slow.iloc[-1]) if not pd.isna(e_slow.iloc[-1]) and e_slow.iloc[-1] > 0 else 0.0
    slope_5 = float(closes.iloc[-1] - closes.iloc[-5]) / closes.iloc[-5] if len(closes) >= 5 else 0.0
    atr_pct = float(atr_v.iloc[-1] / last) if last > 0 and not pd.isna(atr_v.iloc[-1]) else 0.0
    bb_w_rank = float(bb_w.rolling(200).rank(pct=True).iloc[-1]) if bb_w.notna().sum() > 10 else 0.5
    rv_rank = float(rv.rolling(200).rank(pct=True).iloc[-1]) if rv.notna().sum() > 10 else 0.5
    vol_z = float(zscore(df["volume"], 50).iloc[-1]) if len(df) > 60 else 0.0

    return {
        "last_price": last, "last_ret": last_ret,
        "trend_score": round(trend_score, 5),
        "slope_5": round(slope_5, 5),
        "atr_pct": round(atr_pct, 5),
        "bb_width_pct": round(bb_w_rank, 4),
        "rv_pct": round(rv_rank, 4),
        "volume_zscore": round(vol_z, 4),
    }


def _classify(f: dict) -> tuple[str, float]:
    """Return (regime, confidence in [0,1])."""
    # Strong shock
    if abs(f["last_ret"]) > 0.04:
        return ("panic_down" if f["last_ret"] < 0 else "euphoric_up",
                min(1.0, abs(f["last_ret"]) * 10))
    # Volatility expansion or compression
    if f["bb_width_pct"] < 0.15 and f["rv_pct"] < 0.35:
        return ("compression", 1.0 - f["bb_width_pct"])
    if f["bb_width_pct"] > 0.85 and f["rv_pct"] > 0.75:
        return ("expansion", min(1.0, f["bb_width_pct"]))
    # Recent shock decay → post_shock
    if 0.015 <= abs(f["last_ret"]) <= 0.04 and f["rv_pct"] > 0.7:
        return "post_shock", 0.6
    # Trend up/down with healthy vol
    if f["trend_score"] > 0.015 and f["slope_5"] > 0:
        return "trend_up", min(1.0, 0.4 + 10 * abs(f["trend_score"]))
    if f["trend_score"] < -0.015 and f["slope_5"] < 0:
        return "trend_down", min(1.0, 0.4 + 10 * abs(f["trend_score"]))
    return "range_chop", 0.5


def detect_regime_v2(df: Optional[pd.DataFrame] = None, *, persist: bool = True) -> dict:
    if df is None:
        df = load_candles(limit=500)
    if df.empty or len(df) < 50:
        return {"regime": "unknown", "confidence": 0.0, "features": {}}
    f = _features(df)
    regime, conf = _classify(f)
    out = {"regime": regime, "confidence": round(float(conf), 4), "features": f,
           "ts": int(time.time())}
    if persist:
        with session_scope() as s:
            s.add(RegimeSnapshot(ts=out["ts"], regime=regime,
                                  confidence=out["confidence"], features=f))
    return out


def recent_regimes(limit: int = 100) -> list[dict]:
    with session_scope() as s:
        rows = s.query(RegimeSnapshot).order_by(RegimeSnapshot.ts.desc()).limit(limit).all()
        return [{"id": r.id, "ts": r.ts, "regime": r.regime,
                 "confidence": r.confidence, "features": r.features} for r in rows]
