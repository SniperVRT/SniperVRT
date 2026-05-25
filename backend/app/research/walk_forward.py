"""Walk-forward (rolling-window) out-of-sample evaluation.

Given a strategy and a price series, slide a `window_size` window across history
and run a backtest inside each window. Aggregate per-window Sharpe / return /
max-DD, then compute a stability score: positive if the strategy keeps producing
similar sign and magnitude across windows.

Use this to detect overfit strategies whose backtest looks great only on the
specific aggregate period.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

from backend.app.backtest.engine import run_backtest
from backend.app.core.db import session_scope
from backend.app.data import load_candles
from backend.app.models import WalkForwardRun
from backend.app.strategies import build_strategy


def _stability(sharpes: list[float]) -> float:
    if not sharpes:
        return 0.0
    arr = np.array(sharpes)
    mean = arr.mean()
    std = arr.std(ddof=0)
    # Coefficient-of-variation–like stability; bounded to [0,1]
    if mean == 0:
        return 0.0
    cv = std / abs(mean)
    return float(max(0.0, min(1.0, 1.0 - cv / 2.0)))


def walk_forward(strategy_type: str, params: Optional[dict] = None, *,
                  window_size: int = 500, step_size: int = 100,
                  starting_equity: float = 10_000.0,
                  persist: bool = True) -> dict:
    df = load_candles(limit=window_size * 10)
    if df.empty or len(df) < window_size + step_size:
        return {"ok": False, "reason": f"need ≥ {window_size + step_size} candles"}
    folds = []
    i = 0
    while i + window_size <= len(df):
        sub = df.iloc[i:i + window_size]
        strat = build_strategy(strategy_type, params)
        res = run_backtest(strat, sub, starting_equity=starting_equity)
        folds.append({
            "start_idx": i, "end_idx": i + window_size,
            "start_ts": int(sub["ts"].iloc[0]) if "ts" in sub.columns else 0,
            "end_ts": int(sub["ts"].iloc[-1]) if "ts" in sub.columns else 0,
            "total_return_pct": res.total_return_pct,
            "sharpe": res.sharpe,
            "max_drawdown_pct": res.max_drawdown_pct,
            "num_trades": res.num_trades,
            "win_rate": res.win_rate,
            "profit_factor": res.profit_factor,
        })
        i += step_size
    if not folds:
        return {"ok": False, "reason": "no folds produced"}
    sharpes = [f["sharpe"] for f in folds]
    rets = [f["total_return_pct"] for f in folds]
    dds = [f["max_drawdown_pct"] for f in folds]
    aggregate = {
        "mean_sharpe": round(float(np.mean(sharpes)), 4),
        "median_sharpe": round(float(np.median(sharpes)), 4),
        "std_sharpe": round(float(np.std(sharpes, ddof=0)), 4),
        "min_sharpe": round(float(np.min(sharpes)), 4),
        "max_sharpe": round(float(np.max(sharpes)), 4),
        "frac_positive_sharpe": round(float(np.mean([1.0 if s > 0 else 0.0 for s in sharpes])), 4),
        "mean_return_pct": round(float(np.mean(rets)), 6),
        "worst_drawdown": round(float(np.min(dds)), 6),
        "n_folds": len(folds),
    }
    stability = _stability(sharpes)
    accepted = (aggregate["mean_sharpe"] > 0.4 and
                aggregate["frac_positive_sharpe"] >= 0.6 and
                stability >= 0.4)

    if persist:
        with session_scope() as s:
            s.add(WalkForwardRun(
                strategy_type=strategy_type, params=params or {},
                window_size=window_size, step_size=step_size,
                folds=folds, aggregate=aggregate,
                stability=stability, accepted=accepted,
            ))
    return {"ok": True, "strategy_type": strategy_type, "params": params or {},
            "window_size": window_size, "step_size": step_size,
            "folds": folds, "aggregate": aggregate,
            "stability": stability, "accepted": accepted}


def recent_walk_forwards(limit: int = 20) -> list[dict]:
    with session_scope() as s:
        rows = s.query(WalkForwardRun).order_by(WalkForwardRun.id.desc()).limit(limit).all()
        return [{
            "id": r.id, "strategy_type": r.strategy_type, "params": r.params,
            "window_size": r.window_size, "step_size": r.step_size,
            "aggregate": r.aggregate, "stability": r.stability,
            "accepted": r.accepted,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows]
