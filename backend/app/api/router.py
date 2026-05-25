"""All REST routes. Frontend talks to these endpoints exclusively."""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.app.backtest.engine import run_backtest
from backend.app.core.config import REPORT_DIR, get_config, get_settings, reload_config
from backend.app.core.db import session_scope
from backend.app.data import data_quality, ensure_dataset, latest_price, load_candles
from backend.app.governance.validator import generate_council_decision, live_readiness_score
from backend.app.learning.tournament import parameter_sweep, run_tournament
from backend.app.live.gate import get_live_gate
from backend.app.models import (
    BacktestRun, EventLog, GovernanceDecision, PaperAccount, Position,
    RiskBlock, StrategyConfig, Trade,
)
from backend.app.paper.runtime import get_runtime
from backend.app.strategies import (
    STRATEGY_REGISTRY, build_strategy, detect_regime, list_strategies,
)

log = logging.getLogger(__name__)
api_router = APIRouter()


# ============================ Health ============================
@api_router.get("/health")
def health():
    return {"status": "ok", "ts": int(time.time())}


@api_router.get("/system/status")
def system_status():
    cfg = get_config()
    s = get_settings()
    runtime = get_runtime()
    paper_status = runtime.status()
    live = get_live_gate().status()
    dq = data_quality().to_dict()
    return {
        "ts": int(time.time()),
        "mode": "paper" if not live["ready_for_live"] else "live_unlocked",
        "live": live,
        "paper": paper_status,
        "data_quality": dq,
        "kill_switch": s.kill_switch,
        "config": {
            "symbol": cfg.data.default_symbol,
            "timeframe": cfg.data.default_timeframe,
            "exchange": cfg.data.default_exchange,
            "starting_equity": cfg.paper.starting_equity,
            "tick_seconds": cfg.paper.tick_seconds,
        },
    }


# ============================ Market data ============================
@api_router.post("/data/refresh")
def data_refresh(symbol: Optional[str] = None, timeframe: Optional[str] = None,
                 exchange: Optional[str] = None, limit: Optional[int] = None):
    res = ensure_dataset(exchange=exchange, symbol=symbol, timeframe=timeframe, limit=limit)
    return res


@api_router.get("/data/candles")
def candles(symbol: Optional[str] = None, timeframe: Optional[str] = None,
            exchange: Optional[str] = None, limit: int = 500):
    df = load_candles(exchange=exchange, symbol=symbol, timeframe=timeframe, limit=limit)
    if df.empty:
        return {"candles": [], "count": 0}
    return {
        "count": len(df),
        "candles": [{
            "ts": int(r.ts), "open": r.open, "high": r.high, "low": r.low,
            "close": r.close, "volume": r.volume,
        } for r in df.itertuples()],
    }


@api_router.get("/data/quality")
def quality(symbol: Optional[str] = None, timeframe: Optional[str] = None):
    return data_quality(symbol=symbol, timeframe=timeframe).to_dict()


@api_router.get("/data/regime")
def regime(symbol: Optional[str] = None, timeframe: Optional[str] = None):
    df = load_candles(symbol=symbol, timeframe=timeframe)
    return detect_regime(df)


@api_router.get("/data/price")
def price(symbol: Optional[str] = None, timeframe: Optional[str] = None):
    return {"price": latest_price(symbol=symbol, timeframe=timeframe), "ts": int(time.time())}


# ============================ Strategies ============================
@api_router.get("/strategies")
def get_strategies():
    base = list_strategies()
    with session_scope() as s:
        saved = {c.name: c for c in s.query(StrategyConfig).all()}
    out = []
    for b in base:
        cfg = saved.get(b["name"])
        out.append({
            **b,
            "enabled": cfg.enabled if cfg else True,
            "saved_params": cfg.params if cfg else None,
        })
    return {"strategies": out}


class StrategyUpsert(BaseModel):
    name: str
    strategy_type: str
    params: dict = Field(default_factory=dict)
    enabled: bool = True


@api_router.post("/strategies")
def upsert_strategy(payload: StrategyUpsert):
    if payload.strategy_type not in STRATEGY_REGISTRY:
        raise HTTPException(400, f"unknown strategy_type: {payload.strategy_type}")
    with session_scope() as s:
        cfg = s.query(StrategyConfig).filter_by(name=payload.name).one_or_none()
        if cfg:
            cfg.strategy_type = payload.strategy_type
            cfg.params = payload.params
            cfg.enabled = payload.enabled
        else:
            cfg = StrategyConfig(name=payload.name, strategy_type=payload.strategy_type,
                                  params=payload.params, enabled=payload.enabled)
            s.add(cfg)
    return {"ok": True, "name": payload.name}


@api_router.delete("/strategies/{name}")
def delete_strategy(name: str):
    with session_scope() as s:
        cfg = s.query(StrategyConfig).filter_by(name=name).one_or_none()
        if cfg:
            s.delete(cfg)
    return {"ok": True}


# ============================ Backtests ============================
class BacktestRequest(BaseModel):
    strategy_type: str
    strategy_name: Optional[str] = None
    params: dict = Field(default_factory=dict)
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    limit: Optional[int] = 2000
    starting_equity: float = 10_000.0
    allow_short: bool = True


@api_router.post("/backtest/run")
def backtest_run(req: BacktestRequest):
    if req.strategy_type not in STRATEGY_REGISTRY:
        raise HTTPException(400, f"unknown strategy_type: {req.strategy_type}")
    df = load_candles(symbol=req.symbol, timeframe=req.timeframe, limit=req.limit)
    if df.empty:
        ensure_dataset(symbol=req.symbol, timeframe=req.timeframe)
        df = load_candles(symbol=req.symbol, timeframe=req.timeframe, limit=req.limit)
    if df.empty:
        raise HTTPException(400, "no data — call /api/data/refresh first")
    strat = build_strategy(req.strategy_type, req.params)
    result = run_backtest(strat, df, starting_equity=req.starting_equity, allow_short=req.allow_short)
    name = req.strategy_name or req.strategy_type
    with session_scope() as s:
        run = BacktestRun(
            strategy_name=name, strategy_type=req.strategy_type, params=req.params,
            symbol=req.symbol or get_config().data.default_symbol,
            timeframe=req.timeframe or get_config().data.default_timeframe,
            start_ts=int(df["ts"].iloc[0]) if "ts" in df.columns else 0,
            end_ts=int(df["ts"].iloc[-1]) if "ts" in df.columns else 0,
            starting_equity=result.starting_equity,
            final_equity=result.final_equity,
            total_return_pct=result.total_return_pct,
            sharpe=result.sharpe, max_drawdown_pct=result.max_drawdown_pct,
            win_rate=result.win_rate, profit_factor=result.profit_factor,
            num_trades=result.num_trades, avg_trade_pct=result.avg_trade_pct,
            expectancy=result.expectancy, metrics=result.metrics,
            equity_curve=result.equity_curve[-1000:], trades_log=result.trades[-500:],
        )
        s.add(run)
        s.flush()
        run_id = run.id
    return {"run_id": run_id, **result.to_dict()}


@api_router.get("/backtest/runs")
def backtest_runs(limit: int = 25):
    with session_scope() as s:
        rows = s.query(BacktestRun).order_by(BacktestRun.id.desc()).limit(limit).all()
        return {"runs": [{
            "id": r.id, "strategy_name": r.strategy_name, "strategy_type": r.strategy_type,
            "params": r.params, "symbol": r.symbol, "timeframe": r.timeframe,
            "total_return_pct": r.total_return_pct, "sharpe": r.sharpe,
            "max_drawdown_pct": r.max_drawdown_pct, "win_rate": r.win_rate,
            "profit_factor": r.profit_factor, "num_trades": r.num_trades,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows]}


@api_router.get("/backtest/runs/{run_id}")
def backtest_run_detail(run_id: int):
    with session_scope() as s:
        r = s.get(BacktestRun, run_id)
        if r is None:
            raise HTTPException(404, "not found")
        return {
            "id": r.id, "strategy_name": r.strategy_name, "params": r.params,
            "metrics": r.metrics, "equity_curve": r.equity_curve, "trades": r.trades_log,
            "total_return_pct": r.total_return_pct, "sharpe": r.sharpe,
            "max_drawdown_pct": r.max_drawdown_pct, "win_rate": r.win_rate,
            "profit_factor": r.profit_factor, "num_trades": r.num_trades,
        }


@api_router.get("/backtest/runs/{run_id}/export.json")
def backtest_export(run_id: int):
    with session_scope() as s:
        r = s.get(BacktestRun, run_id)
        if r is None:
            raise HTTPException(404, "not found")
        path = REPORT_DIR / f"backtest_{run_id}.json"
        path.write_text(json.dumps({
            "id": r.id, "strategy_name": r.strategy_name, "params": r.params,
            "metrics": r.metrics, "trades": r.trades_log, "equity_curve": r.equity_curve,
            "total_return_pct": r.total_return_pct, "sharpe": r.sharpe,
            "max_drawdown_pct": r.max_drawdown_pct, "win_rate": r.win_rate,
            "profit_factor": r.profit_factor, "num_trades": r.num_trades,
        }, indent=2, default=str))
        return {"path": str(path), "bytes": path.stat().st_size}


# ============================ Paper trading ============================
@api_router.get("/paper/status")
def paper_status():
    return get_runtime().status()


@api_router.get("/paper/equity-curve")
def paper_equity_curve(limit: int = 500):
    return {"points": get_runtime().equity_curve(limit=limit)}


@api_router.post("/paper/start")
def paper_start():
    return get_runtime().start()


@api_router.post("/paper/stop")
def paper_stop():
    return get_runtime().stop()


@api_router.post("/paper/tick")
def paper_tick():
    """Manually advance one tick (useful for tests / when not running the loop)."""
    return get_runtime().tick_once()


@api_router.post("/paper/reset")
def paper_reset():
    return get_runtime().reset()


class SetActiveStrategy(BaseModel):
    name: str


@api_router.post("/paper/active-strategy")
def paper_set_active(req: SetActiveStrategy):
    try:
        get_runtime().set_active_strategy(req.name)
    except KeyError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "active_strategy": req.name}


@api_router.get("/paper/positions")
def paper_positions(open_only: bool = True, limit: int = 100):
    with session_scope() as s:
        acc = s.query(PaperAccount).filter_by(name="default").one_or_none()
        if acc is None:
            return {"positions": []}
        q = s.query(Position).filter_by(account_id=acc.id)
        if open_only:
            q = q.filter_by(is_open=True)
        rows = q.order_by(Position.id.desc()).limit(limit).all()
        return {"positions": [{
            "id": p.id, "symbol": p.symbol, "side": p.side, "size": p.size,
            "entry": p.entry_price, "stop": p.stop_price, "tp": p.take_profit,
            "is_open": p.is_open, "opened_at": p.opened_at, "closed_at": p.closed_at,
            "exit_price": p.exit_price, "pnl": p.pnl, "pnl_pct": p.pnl_pct,
            "strategy": p.strategy,
        } for p in rows]}


@api_router.get("/paper/trades")
def paper_trades(limit: int = 200):
    with session_scope() as s:
        acc = s.query(PaperAccount).filter_by(name="default").one_or_none()
        if acc is None:
            return {"trades": []}
        rows = s.query(Trade).filter_by(account_id=acc.id).order_by(Trade.ts.desc()).limit(limit).all()
        return {"trades": [{
            "id": t.id, "ts": t.ts, "symbol": t.symbol, "side": t.side,
            "price": t.price, "size": t.size, "fee": t.fee, "pnl": t.pnl,
            "strategy": t.strategy, "reason": t.reason, "mode": t.mode,
        } for t in rows]}


# ============================ Risk ============================
@api_router.get("/risk/blocks")
def risk_blocks(limit: int = 100):
    with session_scope() as s:
        rows = s.query(RiskBlock).order_by(RiskBlock.id.desc()).limit(limit).all()
        return {"blocks": [{
            "id": b.id, "ts": b.ts, "rule": b.rule, "detail": b.detail,
            "strategy": b.strategy, "payload": b.payload,
        } for b in rows]}


@api_router.get("/risk/limits")
def risk_limits():
    return get_config().risk.model_dump()


# ============================ Governance / Reports ============================
@api_router.post("/governance/validate")
def governance_validate():
    return generate_council_decision()


@api_router.get("/governance/readiness")
def governance_readiness():
    return live_readiness_score()


@api_router.get("/governance/decisions")
def governance_decisions(limit: int = 25):
    with session_scope() as s:
        rows = s.query(GovernanceDecision).order_by(GovernanceDecision.id.desc()).limit(limit).all()
        return {"decisions": [{
            "id": d.id, "ts": d.ts, "category": d.category, "decision": d.decision,
            "rationale": d.rationale, "live_readiness_score": d.live_readiness_score,
        } for d in rows]}


@api_router.get("/reports")
def list_reports():
    out = []
    if REPORT_DIR.exists():
        for p in sorted(REPORT_DIR.glob("*.json")):
            try:
                stat = p.stat()
                out.append({"name": p.name, "bytes": stat.st_size, "mtime": int(stat.st_mtime)})
            except OSError:
                pass
    return {"reports": out}


@api_router.get("/reports/{name}")
def get_report(name: str):
    p = REPORT_DIR / name
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "not found")
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {"raw": p.read_text()}


# ============================ Learning ============================
@api_router.post("/learning/tournament")
def learning_tournament(symbol: Optional[str] = None, timeframe: Optional[str] = None):
    return run_tournament(symbol=symbol, timeframe=timeframe)


class SweepRequest(BaseModel):
    strategy: str
    grid: dict


@api_router.post("/learning/sweep")
def learning_sweep(req: SweepRequest):
    if req.strategy not in STRATEGY_REGISTRY:
        raise HTTPException(400, f"unknown strategy: {req.strategy}")
    return parameter_sweep(req.strategy, req.grid)


# ============================ Live (locked) ============================
@api_router.get("/live/status")
def live_status():
    return get_live_gate().status()


class UnlockRequest(BaseModel):
    reason: str
    confirm: str = ""


@api_router.post("/live/unlock")
def live_unlock(req: UnlockRequest):
    if req.confirm != "I_ACCEPT_LIVE_RISK":
        raise HTTPException(400, "confirm string must be exactly 'I_ACCEPT_LIVE_RISK'")
    return get_live_gate().unlock(req.reason)


@api_router.post("/live/relock")
def live_relock():
    return get_live_gate().relock("api_call")


@api_router.post("/live/emergency-shutdown")
def live_emergency():
    return get_live_gate().emergency_shutdown()


# ============================ Events ============================
@api_router.get("/events")
def list_events(limit: int = 100, category: Optional[str] = None):
    with session_scope() as s:
        q = s.query(EventLog)
        if category:
            q = q.filter(EventLog.category == category)
        rows = q.order_by(EventLog.id.desc()).limit(limit).all()
        return {"events": [{
            "id": e.id, "ts": e.ts, "level": e.level, "category": e.category,
            "message": e.message, "payload": e.payload,
        } for e in rows]}


# ============================ Settings ============================
@api_router.get("/settings")
def get_settings_api():
    cfg = get_config()
    s = get_settings()
    return {
        "config": cfg.model_dump(),
        "runtime": {
            "db_url": s.db_url,
            "log_level": s.log_level,
            "kill_switch": s.kill_switch,
            "host": s.host,
            "port": s.port,
        },
    }


@api_router.post("/settings/reload")
def settings_reload():
    cfg = reload_config()
    return {"ok": True, "config": cfg.model_dump()}
