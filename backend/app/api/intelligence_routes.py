"""Phase 2 API: intelligence, research, memory, scheduler, explainability."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.intelligence import (
    attribute_market_reactions, collect_micro_signals, detect_regime_v2,
    ingest_news, recent_micro, recent_news, recent_regimes, recent_sentiment,
    snapshot_sentiment,
)
from backend.app.memory import recall, recent, recent_traces, score_for_subject
from backend.app.research import (
    discover_edges, monte_carlo_run, recent_edges, recent_monte_carlo,
    recent_walk_forwards, run_agent, run_all_agents, walk_forward, AGENTS,
)
from backend.app.research.agents import recent_findings
from backend.app.scheduler import get_scheduler

router = APIRouter(prefix="/intel", tags=["intelligence"])


# -------- News --------
@router.post("/news/ingest")
def news_ingest(force_synthetic: bool = False):
    return ingest_news(force_synthetic=force_synthetic)


@router.get("/news")
def news_list(limit: int = 50, topic: Optional[str] = None, min_importance: float = 0.0):
    return {"items": recent_news(limit=limit, topic=topic,
                                  min_importance=min_importance)}


@router.post("/news/attribute")
def news_attribute():
    return attribute_market_reactions()


# -------- Sentiment --------
@router.post("/sentiment/snapshot")
def sentiment_snap():
    return snapshot_sentiment()


@router.get("/sentiment")
def sentiment_list(limit: int = 100, source: Optional[str] = None):
    return {"snapshots": recent_sentiment(limit=limit, source=source)}


# -------- Microstructure --------
@router.post("/micro/collect")
def micro_collect():
    return collect_micro_signals()


@router.get("/micro")
def micro_list(name: Optional[str] = None, limit: int = 200):
    return {"signals": recent_micro(name=name, limit=limit)}


# -------- Regime v2 --------
@router.post("/regime/snapshot")
def regime_snap():
    return detect_regime_v2()


@router.get("/regime/history")
def regime_history(limit: int = 100):
    return {"regimes": recent_regimes(limit=limit)}


# -------- Edges --------
@router.post("/edges/discover")
def edges_discover():
    return discover_edges()


@router.get("/edges")
def edges_list(accepted_only: bool = False, limit: int = 50):
    return {"edges": recent_edges(accepted_only=accepted_only, limit=limit)}


# -------- Walk-forward --------
class WalkForwardRequest(BaseModel):
    strategy_type: str
    params: dict = Field(default_factory=dict)
    window_size: int = 500
    step_size: int = 100
    starting_equity: float = 10_000.0


@router.post("/walk-forward/run")
def walk_forward_run(req: WalkForwardRequest):
    return walk_forward(req.strategy_type, req.params,
                        window_size=req.window_size, step_size=req.step_size,
                        starting_equity=req.starting_equity)


@router.get("/walk-forward")
def walk_forward_list(limit: int = 20):
    return {"runs": recent_walk_forwards(limit=limit)}


# -------- Monte Carlo --------
class MonteCarloRequest(BaseModel):
    run_id: int
    n_samples: int = 2000


@router.post("/monte-carlo/run")
def mc_run(req: MonteCarloRequest):
    res = monte_carlo_run(req.run_id, n_samples=req.n_samples)
    if not res.get("ok"):
        raise HTTPException(400, res.get("reason", "monte carlo failed"))
    return res


@router.get("/monte-carlo")
def mc_list(limit: int = 20):
    return {"runs": recent_monte_carlo(limit=limit)}


# -------- Agents / Findings --------
@router.get("/agents")
def agents_list():
    return {"agents": list(AGENTS.keys())}


@router.post("/agents/run-all")
def agents_run_all():
    return run_all_agents()


@router.post("/agents/{name}/run")
def agents_run_one(name: str):
    try:
        return {"ok": True, "result": run_agent(name)}
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.get("/findings")
def findings_list(limit: int = 50, agent: Optional[str] = None,
                  min_score: float = 0.0):
    return {"findings": recent_findings(limit=limit, agent=agent,
                                         min_score=min_score)}


# -------- Memory --------
@router.get("/memory")
def memory_list(limit: int = 50, kind: Optional[str] = None,
                subject: Optional[str] = None):
    return {"entries": recall(
        kinds=[kind] if kind else None,
        subject=subject, limit=limit,
    )}


@router.get("/memory/score/{subject}")
def memory_score(subject: str, half_life_days: float = 14.0):
    return score_for_subject(subject, half_life_days=half_life_days)


# -------- Decision traces (explainability) --------
@router.get("/traces")
def traces_list(limit: int = 50, kind: Optional[str] = None,
                subject: Optional[str] = None):
    return {"traces": recent_traces(limit=limit, kind=kind, subject=subject)}


# -------- Scheduler --------
@router.get("/scheduler/status")
def scheduler_status():
    return get_scheduler().status()


@router.post("/scheduler/start")
def scheduler_start():
    return get_scheduler().start()


@router.post("/scheduler/stop")
def scheduler_stop():
    return get_scheduler().stop()


@router.post("/scheduler/run/{task}")
def scheduler_force(task: str):
    try:
        return get_scheduler().force_run(task)
    except KeyError as e:
        raise HTTPException(404, str(e))
