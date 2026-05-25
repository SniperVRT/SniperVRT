"""Market microstructure / flow signals.

Tries CCXT for funding rate, open interest, and recent liquidations if the
exchange supports them. Falls back to local derivations from candle data:
  - vol_zscore: realized-volatility z-score (look-back 200)
  - range_z: candle range / ATR z-score
  - volume_z: volume z-score
  - close_zscore: price z-score vs 50-bar mean
  - dollar_volume: close * volume

All signals are persisted to `micro_signals` with a z-score so edge discovery
can use them as features uniformly.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Optional

import numpy as np
import pandas as pd

from backend.app.core.config import get_config
from backend.app.core.db import session_scope
from backend.app.data import load_candles
from backend.app.models import MicroSignal
from backend.app.strategies.indicators import atr, realized_vol, zscore

log = logging.getLogger(__name__)


def _ccxt_funding(symbol: str, exchange_id: str) -> Optional[float]:
    try:
        import ccxt
        klass = getattr(ccxt, exchange_id)
        ex = klass({"enableRateLimit": True, "timeout": 4000,
                    "options": {"defaultType": "future"}})
        if not getattr(ex, "has", {}).get("fetchFundingRate"):
            return None
        fr = ex.fetchFundingRate(symbol)
        return float(fr.get("fundingRate")) if fr.get("fundingRate") is not None else None
    except Exception as e:
        log.debug("ccxt funding failed: %s", e)
        return None


def _ccxt_open_interest(symbol: str, exchange_id: str) -> Optional[float]:
    try:
        import ccxt
        klass = getattr(ccxt, exchange_id)
        ex = klass({"enableRateLimit": True, "timeout": 4000,
                    "options": {"defaultType": "future"}})
        if not hasattr(ex, "fetchOpenInterest"):
            return None
        oi = ex.fetchOpenInterest(symbol)
        return float(oi.get("openInterestValue") or oi.get("openInterest")) if oi else None
    except Exception as e:
        log.debug("ccxt OI failed: %s", e)
        return None


def _persist(name: str, value: float, z: Optional[float], meta: Optional[dict] = None) -> int:
    with session_scope() as s:
        sig = MicroSignal(
            ts=int(time.time()), name=name, value=float(value),
            zscore=float(z) if z is not None and not math.isnan(z) else None,
            meta=meta or {},
        )
        s.add(sig)
        s.flush()
        return sig.id


def collect_micro_signals() -> dict:
    """Compute every micro-signal we can, persist them, return summary."""
    cfg = get_config()
    df = load_candles(limit=500)
    out: list[dict] = []
    if df.empty:
        return {"ok": False, "reason": "no price data"}

    closes = df["close"]
    vols = df["volume"]

    rv = realized_vol(closes, 24).iloc[-1]
    rv_z = zscore(realized_vol(closes, 24), 100).iloc[-1]
    out.append({"name": "vol_zscore",
                "id": _persist("vol_zscore", float(rv if not pd.isna(rv) else 0.0),
                               float(rv_z) if not pd.isna(rv_z) else None,
                               {"window": 24})})

    atr_v = atr(df, 14).iloc[-1]
    atr_z = zscore(atr(df, 14), 100).iloc[-1]
    out.append({"name": "atr_zscore",
                "id": _persist("atr_zscore", float(atr_v if not pd.isna(atr_v) else 0.0),
                               float(atr_z) if not pd.isna(atr_z) else None,
                               {"window": 14})})

    vz = zscore(vols, 50).iloc[-1]
    out.append({"name": "volume_zscore",
                "id": _persist("volume_zscore", float(vols.iloc[-1]),
                               float(vz) if not pd.isna(vz) else None,
                               {"window": 50})})

    cz = zscore(closes, 50).iloc[-1]
    out.append({"name": "price_zscore",
                "id": _persist("price_zscore", float(closes.iloc[-1]),
                               float(cz) if not pd.isna(cz) else None,
                               {"window": 50})})

    dv = float(closes.iloc[-1] * vols.iloc[-1])
    out.append({"name": "dollar_volume",
                "id": _persist("dollar_volume", dv, None, {})})

    # Try live funding & OI; record only if we got something
    sym = cfg.data.default_symbol
    fr = _ccxt_funding(sym, cfg.data.default_exchange)
    if fr is not None:
        out.append({"name": "funding_rate", "value": fr,
                    "id": _persist("funding_rate", fr, None, {})})
    oi = _ccxt_open_interest(sym, cfg.data.default_exchange)
    if oi is not None:
        out.append({"name": "open_interest", "value": oi,
                    "id": _persist("open_interest", oi, None, {})})

    return {"ok": True, "collected": out, "count": len(out)}


def recent_micro(*, name: Optional[str] = None, limit: int = 200) -> list[dict]:
    with session_scope() as s:
        q = s.query(MicroSignal)
        if name:
            q = q.filter(MicroSignal.name == name)
        rows = q.order_by(MicroSignal.ts.desc()).limit(limit).all()
        return [{"id": m.id, "ts": m.ts, "name": m.name,
                 "value": m.value, "zscore": m.zscore, "meta": m.meta}
                for m in rows]
