"""Pure-pandas indicators. Vectorized so the same code drives backtests and live signals."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length, min_periods=length).mean()


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    roll_up = up.ewm(alpha=1 / length, adjust=False).mean()
    roll_down = down.ewm(alpha=1 / length, adjust=False).mean()
    rs = roll_up / roll_down.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def bollinger(series: pd.Series, length: int = 20, mult: float = 2.0):
    mid = sma(series, length)
    std = series.rolling(length, min_periods=length).std()
    upper = mid + mult * std
    lower = mid - mult * std
    return lower, mid, upper


def donchian(df: pd.DataFrame, length: int = 20):
    upper = df["high"].rolling(length).max()
    lower = df["low"].rolling(length).min()
    return lower, upper


def zscore(series: pd.Series, length: int = 50) -> pd.Series:
    mean = series.rolling(length).mean()
    std = series.rolling(length).std()
    return (series - mean) / std.replace(0, np.nan)


def realized_vol(series: pd.Series, length: int = 24) -> pd.Series:
    """Annualized realized vol on log returns (length bars of look-back)."""
    rets = np.log(series / series.shift(1))
    return rets.rolling(length).std()
