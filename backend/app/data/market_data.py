"""Market data: fetches from exchanges via CCXT, falls back to synthetic GBM if no network.

All candles are persisted in SQLite. Duplicate and missing-candle detection runs on every load.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from backend.app.core.config import get_config
from backend.app.core.db import session_scope
from backend.app.models import Candle

log = logging.getLogger(__name__)


TIMEFRAME_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}


@dataclass
class DataQuality:
    candle_count: int
    missing_candles: int
    duplicate_candles: int
    coverage_pct: float           # 1.0 == perfect
    first_ts: int
    last_ts: int

    def to_dict(self) -> dict:
        return {
            "candle_count": self.candle_count,
            "missing_candles": self.missing_candles,
            "duplicate_candles": self.duplicate_candles,
            "coverage_pct": round(self.coverage_pct, 4),
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
        }


def _ccxt_fetch(exchange_id: str, symbol: str, timeframe: str, limit: int) -> Optional[list]:
    try:
        import ccxt
        klass = getattr(ccxt, exchange_id)
        ex = klass({"enableRateLimit": True, "timeout": 8000})
        ohlcv = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        return ohlcv
    except Exception as e:  # network / API / parsing
        log.warning("CCXT fetch failed (%s %s %s): %s", exchange_id, symbol, timeframe, e)
        return None


def _synthetic_candles(symbol: str, timeframe: str, limit: int, seed: int = 7) -> list:
    """Generate a deterministic GBM-style BTC price series so the system runs offline."""
    rng = np.random.default_rng(seed)
    tf_sec = TIMEFRAME_SECONDS.get(timeframe, 3600)
    # Align to a fixed grid so re-running ensure_dataset is idempotent.
    now = (int(time.time()) // tf_sec) * tf_sec
    start = now - (limit * tf_sec)
    # Drifty BTC-ish process
    mu = 0.00002
    sigma = 0.012
    # Inject regimes for realism
    regimes = rng.choice([0, 1, 2], size=limit, p=[0.55, 0.25, 0.20])
    price = 30_000.0
    candles = []
    for i in range(limit):
        r = regimes[i]
        regime_drift = {0: 0.00005, 1: -0.0001, 2: 0.0}[int(r)]
        regime_vol = {0: 1.0, 1: 1.3, 2: 2.0}[int(r)]
        ret = rng.normal(mu + regime_drift, sigma * regime_vol)
        o = price
        c = o * math.exp(ret)
        h = max(o, c) * (1 + abs(rng.normal(0, 0.003)))
        l = min(o, c) * (1 - abs(rng.normal(0, 0.003)))
        v = abs(rng.normal(1200, 400))
        ts_ms = (start + i * tf_sec) * 1000
        candles.append([ts_ms, o, h, l, c, v])
        price = c
    return candles


def _persist(exchange: str, symbol: str, timeframe: str, candles: list) -> int:
    """Insert candles, ignore duplicates. Returns number of new rows persisted."""
    inserted = 0
    with session_scope() as s:
        existing_ts = {
            r[0] for r in s.query(Candle.ts).filter(
                Candle.exchange == exchange,
                Candle.symbol == symbol,
                Candle.timeframe == timeframe,
            ).all()
        }
        for c in candles:
            ts_ms, o, h, l, cl, v = c
            ts = int(ts_ms / 1000)
            if ts in existing_ts:
                continue
            s.add(Candle(
                exchange=exchange, symbol=symbol, timeframe=timeframe, ts=ts,
                open=float(o), high=float(h), low=float(l), close=float(cl), volume=float(v),
            ))
            inserted += 1
    return inserted


def _last_candle_ts(exchange: str, symbol: str, timeframe: str) -> Optional[int]:
    with session_scope() as s:
        row = s.query(Candle.ts).filter(
            Candle.exchange == exchange, Candle.symbol == symbol, Candle.timeframe == timeframe,
        ).order_by(Candle.ts.desc()).first()
        return int(row[0]) if row else None


def _synthetic_advance(symbol: str, timeframe: str, from_ts: int, to_ts: int,
                       seed_price: float, seed: int = 11) -> list:
    """Generate candles between (from_ts, to_ts] given a starting price; used to advance the
    synthetic series forward by however many timeframe periods have passed."""
    rng = np.random.default_rng(seed ^ from_ts)
    tf_sec = TIMEFRAME_SECONDS.get(timeframe, 3600)
    candles = []
    price = seed_price
    ts = from_ts + tf_sec
    while ts <= to_ts:
        ret = rng.normal(0.00003, 0.011)
        o = price
        c = o * math.exp(ret)
        h = max(o, c) * (1 + abs(rng.normal(0, 0.003)))
        l = min(o, c) * (1 - abs(rng.normal(0, 0.003)))
        v = abs(rng.normal(1200, 400))
        candles.append([ts * 1000, o, h, l, c, v])
        price = c
        ts += tf_sec
    return candles


def ensure_dataset(
    exchange: Optional[str] = None,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    limit: Optional[int] = None,
) -> dict:
    """Make sure we have data persisted; fetch live or synthesize if missing.

    On subsequent calls with no live access, advance the synthetic series forward in time
    so the paper runtime continues to see new candles arriving.
    """
    cfg = get_config()
    exchange = exchange or cfg.data.default_exchange
    symbol = symbol or cfg.data.default_symbol
    timeframe = timeframe or cfg.data.default_timeframe
    limit = limit or cfg.data.candles_back

    candles = _ccxt_fetch(exchange, symbol, timeframe, limit)
    source = "exchange"
    if not candles:
        if not cfg.data.allow_synthetic_fallback:
            return {"ok": False, "source": "none", "inserted": 0}
        # If we already have a series, advance it forward; else seed with `limit` candles.
        last_ts = _last_candle_ts(exchange, symbol, timeframe)
        tf_sec = TIMEFRAME_SECONDS.get(timeframe, 3600)
        now_aligned = (int(time.time()) // tf_sec) * tf_sec
        if last_ts is None:
            candles = _synthetic_candles(symbol, timeframe, limit)
            source = "synthetic_seed"
        elif last_ts < now_aligned:
            # Advance: get last close, append however many bars are missing.
            with session_scope() as s:
                last_row = s.query(Candle.close).filter(
                    Candle.exchange == exchange, Candle.symbol == symbol,
                    Candle.timeframe == timeframe, Candle.ts == last_ts,
                ).first()
                seed_price = float(last_row[0]) if last_row else 30000.0
            candles = _synthetic_advance(symbol, timeframe, last_ts, now_aligned, seed_price)
            source = "synthetic_advance"
        else:
            candles = []
            source = "synthetic_uptodate"

    inserted = _persist(exchange, symbol, timeframe, candles)
    return {"ok": True, "source": source, "inserted": inserted, "requested": limit}


def load_candles(
    exchange: Optional[str] = None,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    limit: Optional[int] = None,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
) -> pd.DataFrame:
    cfg = get_config()
    exchange = exchange or cfg.data.default_exchange
    symbol = symbol or cfg.data.default_symbol
    timeframe = timeframe or cfg.data.default_timeframe

    with session_scope() as s:
        q = s.query(
            Candle.ts, Candle.open, Candle.high, Candle.low, Candle.close, Candle.volume,
        ).filter(
            Candle.exchange == exchange,
            Candle.symbol == symbol,
            Candle.timeframe == timeframe,
        )
        if start_ts is not None:
            q = q.filter(Candle.ts >= start_ts)
        if end_ts is not None:
            q = q.filter(Candle.ts <= end_ts)
        q = q.order_by(Candle.ts.asc())
        rows = q.all()
    if limit:
        rows = rows[-limit:]
    if not rows:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df.set_index(pd.to_datetime(df["ts"], unit="s"), inplace=True)
    return df


def data_quality(
    exchange: Optional[str] = None,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
) -> DataQuality:
    df = load_candles(exchange, symbol, timeframe)
    if df.empty:
        return DataQuality(0, 0, 0, 0.0, 0, 0)
    tf_sec = TIMEFRAME_SECONDS.get(timeframe or get_config().data.default_timeframe, 3600)
    ts_series = df["ts"].astype(int).tolist()
    expected = (ts_series[-1] - ts_series[0]) // tf_sec + 1
    missing = max(0, expected - len(ts_series))
    duplicates = len(ts_series) - len(set(ts_series))
    coverage = min(1.0, len(ts_series) / max(1, expected))
    return DataQuality(
        candle_count=len(ts_series),
        missing_candles=int(missing),
        duplicate_candles=int(duplicates),
        coverage_pct=float(coverage),
        first_ts=int(ts_series[0]),
        last_ts=int(ts_series[-1]),
    )


def latest_price(exchange: Optional[str] = None, symbol: Optional[str] = None,
                 timeframe: Optional[str] = None) -> Optional[float]:
    df = load_candles(exchange, symbol, timeframe, limit=1)
    if df.empty:
        return None
    return float(df["close"].iloc[-1])
