"""Bootstrap-resample trades from a backtest to produce confidence intervals on
total return, Sharpe, and max drawdown.

The point: a backtest gives you ONE realization. Resampling the trade
sequence (with replacement) tells you whether the headline number is robust or
fragile to trade ordering and a few lucky outliers.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from backend.app.core.db import session_scope
from backend.app.models import BacktestRun, MonteCarloRun


def _percentiles(arr: np.ndarray, pcts=(5, 50, 95)) -> dict:
    return {f"p{p}": float(np.percentile(arr, p)) for p in pcts}


def monte_carlo_run(run_id: int, *, n_samples: int = 2000,
                    persist: bool = True) -> dict:
    with session_scope() as s:
        run = s.get(BacktestRun, run_id)
        if run is None:
            return {"ok": False, "reason": "backtest run not found"}
        trades = list(run.trades_log or [])
        starting_equity = float(run.starting_equity)
    if not trades:
        return {"ok": False, "reason": "no trades in backtest run"}

    pnls = np.array([float(t.get("pnl", 0.0)) for t in trades])
    n = len(pnls)
    rng = np.random.default_rng(seed=hash((run_id, n_samples)) & 0xFFFFFFFF)

    final_eqs = np.empty(n_samples)
    max_dds = np.empty(n_samples)
    sharpes = np.empty(n_samples)
    for k in range(n_samples):
        sample = rng.choice(pnls, size=n, replace=True)
        eq = starting_equity + np.cumsum(sample)
        final_eqs[k] = eq[-1]
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / np.where(peak == 0, 1, peak)
        max_dds[k] = float(dd.min())
        # Simple per-trade Sharpe-like ratio
        std = float(sample.std(ddof=1)) if len(sample) > 1 else 0.0
        sharpes[k] = float((sample.mean() / std) * math.sqrt(n)) if std > 0 else 0.0

    returns = (final_eqs - starting_equity) / starting_equity
    metrics = {
        "return_pct": _percentiles(returns),
        "max_drawdown_pct": _percentiles(max_dds),
        "sharpe": _percentiles(sharpes),
        "prob_profit": float((returns > 0).mean()),
        "prob_loss_gt_10pct": float((returns < -0.10).mean()),
        "prob_loss_gt_20pct": float((returns < -0.20).mean()),
        "median_return_pct": float(np.median(returns)),
        "n_samples": n_samples,
        "n_trades": n,
    }
    if persist:
        with session_scope() as s:
            s.add(MonteCarloRun(source_run_id=run_id,
                                 n_samples=n_samples, metrics=metrics))
    return {"ok": True, "source_run_id": run_id, "metrics": metrics}


def recent_monte_carlo(limit: int = 20) -> list[dict]:
    with session_scope() as s:
        rows = s.query(MonteCarloRun).order_by(MonteCarloRun.id.desc()).limit(limit).all()
        return [{
            "id": m.id, "source_run_id": m.source_run_id,
            "n_samples": m.n_samples, "metrics": m.metrics,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        } for m in rows]
