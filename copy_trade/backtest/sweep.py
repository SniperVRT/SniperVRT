"""Parameter sweep + robust-region detection."""

from __future__ import annotations

import sqlite3
from typing import Iterable

from ..config import CopyTradeSettings, get_settings
from .engine import run_backtest


def parse_range(s: str) -> list[float]:
    parts = [float(x) for x in s.split(",")]
    if len(parts) != 3:
        raise ValueError("range must be lo,hi,step")
    lo, hi, step = parts
    out: list[float] = []
    v = lo
    while v <= hi + 1e-9:
        out.append(round(v, 6))
        v += step
    return out


def run_sweep(conn: sqlite3.Connection,
              param_name: str,
              values: Iterable[float],
              settings: CopyTradeSettings | None = None,
              initial_capital: float = 1000.0) -> list[dict]:
    settings = settings or get_settings()
    results = []
    for v in values:
        upd_settings = settings.model_copy(update={param_name: v})
        res = run_backtest(conn, initial_capital=initial_capital,
                           settings=upd_settings,
                           notes=f"sweep_{param_name}={v}")
        results.append({param_name: v, "sharpe": res.sharpe,
                        "return": res.total_return, "mdd": res.max_drawdown,
                        "ticks": res.n_ticks})
    return results


def robust_region(results: list[dict], threshold: float = 0.0) -> list[float]:
    """Contiguous range where sharpe > threshold."""
    keys = [k for k in (results[0].keys() if results else []) if k != "sharpe"]
    if not keys:
        return []
    pname = keys[0]
    region: list[float] = []
    current: list[float] = []
    for r in results:
        s = r.get("sharpe")
        if s is not None and s > threshold:
            current.append(r[pname])
        else:
            if len(current) > len(region):
                region = current
            current = []
    if len(current) > len(region):
        region = current
    return region
