"""Autonomous research / tournament loops.

run_tournament:    runs each known strategy with default params on the active dataset,
                   records BacktestRun rows, returns leaderboard.
parameter_sweep:   does a coarse grid sweep on one strategy.

These persist all results in DB so the system "remembers" what failed.
"""
from __future__ import annotations

import itertools
import time
from typing import Iterable

from backend.app.backtest.engine import run_backtest
from backend.app.core.db import session_scope
from backend.app.data import load_candles
from backend.app.models import BacktestRun, EventLog, LearningRun
from backend.app.strategies import STRATEGY_REGISTRY, build_strategy


def _persist_run(strategy_name: str, strategy_type: str, params: dict,
                 symbol: str, timeframe: str, result, df) -> int:
    with session_scope() as s:
        run = BacktestRun(
            strategy_name=strategy_name,
            strategy_type=strategy_type,
            params=params,
            symbol=symbol, timeframe=timeframe,
            start_ts=int(df["ts"].iloc[0]) if not df.empty else 0,
            end_ts=int(df["ts"].iloc[-1]) if not df.empty else 0,
            starting_equity=result.starting_equity,
            final_equity=result.final_equity,
            total_return_pct=result.total_return_pct,
            sharpe=result.sharpe,
            max_drawdown_pct=result.max_drawdown_pct,
            win_rate=result.win_rate,
            profit_factor=result.profit_factor,
            num_trades=result.num_trades,
            avg_trade_pct=result.avg_trade_pct,
            expectancy=result.expectancy,
            metrics=result.metrics,
            equity_curve=result.equity_curve[-1000:],  # cap size
            trades_log=result.trades[-500:],
        )
        s.add(run)
        s.flush()
        return run.id


def run_tournament(symbol: str | None = None, timeframe: str | None = None) -> dict:
    """Run all strategies head-to-head on the most recent data, return leaderboard."""
    df = load_candles(symbol=symbol, timeframe=timeframe)
    if df.empty:
        return {"ok": False, "reason": "no data — call /api/data/refresh first"}
    leaderboard: list[dict] = []
    started = time.time()
    with session_scope() as s:
        lr = LearningRun(config={"mode": "tournament", "symbol": symbol, "timeframe": timeframe},
                         status="running")
        s.add(lr)
        s.flush()
        learning_id = lr.id

    for name in STRATEGY_REGISTRY.keys():
        strat = build_strategy(name)
        result = run_backtest(strat, df, starting_equity=10_000.0)
        run_id = _persist_run(name, name, strat.params,
                              symbol or "BTC/USDT", timeframe or "1h", result, df)
        leaderboard.append({
            "strategy": name, "run_id": run_id,
            "sharpe": result.sharpe, "return_pct": result.total_return_pct,
            "max_dd": result.max_drawdown_pct, "trades": result.num_trades,
            "win_rate": result.win_rate, "profit_factor": result.profit_factor,
        })

    leaderboard.sort(key=lambda x: x["sharpe"], reverse=True)
    best = leaderboard[0] if leaderboard else None
    with session_scope() as s:
        lr = s.get(LearningRun, learning_id)
        from datetime import datetime
        lr.ended_at = datetime.utcnow()
        lr.summary = {"leaderboard": leaderboard, "elapsed_s": round(time.time() - started, 3)}
        lr.best_strategy = best["strategy"] if best else None
        lr.best_sharpe = best["sharpe"] if best else None
        lr.status = "done"
        s.add(EventLog(ts=int(time.time()), level="info", category="learning",
                       message=f"tournament finished, best={best['strategy'] if best else 'none'}",
                       payload={"leaderboard": leaderboard}))
    return {"ok": True, "leaderboard": leaderboard, "best": best, "learning_run_id": learning_id}


def parameter_sweep(strategy_name: str, grid: dict[str, Iterable]) -> dict:
    """Grid sweep over `grid` for one strategy. Persists every config."""
    df = load_candles()
    if df.empty:
        return {"ok": False, "reason": "no data"}
    keys = list(grid.keys())
    values_lists = [list(grid[k]) for k in keys]
    runs = []
    for combo in itertools.product(*values_lists):
        params = dict(zip(keys, combo))
        strat = build_strategy(strategy_name, params)
        result = run_backtest(strat, df, starting_equity=10_000.0)
        rid = _persist_run(f"{strategy_name}__{params}", strategy_name, params,
                           "BTC/USDT", "1h", result, df)
        runs.append({"run_id": rid, "params": params, "sharpe": result.sharpe,
                     "return_pct": result.total_return_pct, "trades": result.num_trades})
    runs.sort(key=lambda x: x["sharpe"], reverse=True)
    return {"ok": True, "strategy": strategy_name, "runs": runs, "best": runs[0] if runs else None}
