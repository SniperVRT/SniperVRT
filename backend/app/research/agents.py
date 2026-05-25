"""Research agents — deterministic analytical modules.

Each agent:
  - has a fixed scope (news / macro / sentiment / volatility / regime / strategy / risk)
  - reads observable state from the DB
  - emits ResearchFinding rows with an explicit score + evidence
  - records nothing speculative; only what the data shows

Findings are scored 0..1. Anything above 0.6 is surfaced on the dashboard.
Findings are written to memory.MemoryEntry as well so they feed strategy
selection over time.
"""
from __future__ import annotations

import math
import time
from collections import Counter
from typing import Callable

import numpy as np
import pandas as pd

from backend.app.core.db import session_scope
from backend.app.data import load_candles
from backend.app.memory import remember
from backend.app.models import (
    BacktestRun, EdgeCandidate, MicroSignal, NewsItem, RegimeSnapshot,
    ResearchFinding, RiskBlock, SentimentSnapshot, Trade,
)
from backend.app.strategies import detect_regime
from backend.app.strategies.indicators import atr, realized_vol


def _emit(agent: str, category: str, title: str, body: str,
          score: float, evidence: dict) -> int:
    with session_scope() as s:
        f = ResearchFinding(
            ts=int(time.time()), agent=agent, category=category,
            title=title[:255], body=body, score=float(score),
            evidence=evidence,
        )
        s.add(f)
        s.flush()
        fid = f.id
    if score >= 0.6:
        kind = "insight" if score >= 0.75 else "warning"
        remember(kind, f"{agent}:{category}", title, weight=score,
                 evidence=evidence)
    elif score <= 0.2 and category in ("risk", "drift"):
        remember("warning", f"{agent}:{category}", title, weight=0.5,
                 evidence=evidence)
    return fid


# ---------- agents ----------

def news_agent() -> dict:
    """Summarize last-24h news: how many items, sentiment skew, topics, hot stories."""
    now = int(time.time())
    since = now - 24 * 3600
    with session_scope() as s:
        rows = s.query(NewsItem).filter(NewsItem.ts >= since).all()
    if not rows:
        return {"agent": "news", "findings": 0, "note": "no recent news"}
    n = len(rows)
    sent = float(np.mean([r.sentiment for r in rows]))
    importance = float(np.mean([r.importance for r in rows]))
    topics = Counter(r.topic for r in rows).most_common(5)
    high_impact = sorted(rows, key=lambda r: -r.importance)[:5]
    title = f"news pulse: n={n}, avg_sentiment={sent:+.2f}"
    body = (f"Top topics: {topics}. "
            f"Mean importance: {importance:.2f}. "
            f"High-impact: " + "; ".join(f"[{h.topic}] {h.headline[:60]}" for h in high_impact))
    score = min(1.0, importance + abs(sent) * 0.4)
    return {"agent": "news", "findings": 1,
            "id": _emit("news", "news_pulse", title, body, score,
                         {"n": n, "avg_sentiment": sent, "topics": dict(topics)})}


def sentiment_agent() -> dict:
    with session_scope() as s:
        rows = s.query(SentimentSnapshot).order_by(SentimentSnapshot.ts.desc()).limit(50).all()
    if not rows:
        return {"agent": "sentiment", "findings": 0, "note": "no snapshots"}
    by_source: dict[str, list[float]] = {}
    for r in rows:
        by_source.setdefault(r.source, []).append(float(r.score))
    parts = []
    extremes = []
    for src, scores in by_source.items():
        avg = float(np.mean(scores))
        parts.append(f"{src}={avg:+.2f}")
        if abs(avg) > 0.6:
            extremes.append((src, avg))
    title = "sentiment snapshot — " + ", ".join(parts)
    score = min(1.0, max(0.2, np.mean([abs(np.mean(v)) for v in by_source.values()])))
    return {"agent": "sentiment", "findings": 1,
            "id": _emit("sentiment", "snapshot", title,
                         f"Extremes: {extremes or 'none'}",
                         score, {"by_source": {k: float(np.mean(v)) for k, v in by_source.items()}})}


def macro_agent() -> dict:
    """Surface 24h news in macro / regulation / etf categories with importance ≥ 0.5."""
    now = int(time.time())
    since = now - 72 * 3600
    macro_topics = {"macro", "regulation", "etf", "institution"}
    with session_scope() as s:
        rows = s.query(NewsItem).filter(NewsItem.ts >= since,
                                          NewsItem.importance >= 0.5,
                                          NewsItem.topic.in_(list(macro_topics))).all()
    if not rows:
        return {"agent": "macro", "findings": 0, "note": "no notable macro"}
    sent = float(np.mean([r.sentiment for r in rows]))
    title = f"macro pulse: n={len(rows)}, skew={sent:+.2f}"
    body = "; ".join(f"[{r.topic}] {r.headline[:80]}" for r in rows[:6])
    score = min(1.0, 0.5 + abs(sent) * 0.5)
    return {"agent": "macro", "findings": 1,
            "id": _emit("macro", "macro_pulse", title, body, score,
                         {"count": len(rows), "skew": sent})}


def volatility_agent() -> dict:
    df = load_candles(limit=400)
    if df.empty:
        return {"agent": "volatility", "findings": 0, "note": "no data"}
    atr_v = float(atr(df, 14).iloc[-1])
    last = float(df["close"].iloc[-1])
    atr_pct = atr_v / last if last > 0 else 0
    rv24 = realized_vol(df["close"], 24)
    rv_rank = float(rv24.rolling(200).rank(pct=True).iloc[-1]) if rv24.notna().sum() > 30 else 0.5
    title = f"vol: atr_pct={atr_pct:.3%}, rv_rank={rv_rank:.2f}"
    score = 0.4
    body = ""
    if atr_pct > 0.05:
        body = "Volatility elevated; consider shrinking position sizes."
        score = 0.8
    elif atr_pct < 0.01:
        body = "Volatility compressed; breakout strategies favored."
        score = 0.7
    else:
        body = "Volatility in normal band."
    return {"agent": "volatility", "findings": 1,
            "id": _emit("volatility", "vol_state", title, body, score,
                         {"atr_pct": atr_pct, "rv_rank": rv_rank})}


def regime_agent() -> dict:
    df = load_candles(limit=400)
    if df.empty:
        return {"agent": "regime", "findings": 0, "note": "no data"}
    r1 = detect_regime(df)  # original
    # Look at recent persisted v2 snapshots for stability
    with session_scope() as s:
        rows = s.query(RegimeSnapshot).order_by(RegimeSnapshot.ts.desc()).limit(20).all()
    flips = 0
    last_regime = None
    for r in rows:
        if last_regime is not None and r.regime != last_regime:
            flips += 1
        last_regime = r.regime
    stability = 1.0 - min(1.0, flips / 10.0)
    title = f"regime: {r1['regime']} (stability={stability:.2f})"
    body = f"Recent flips in last 20 snapshots: {flips}."
    return {"agent": "regime", "findings": 1,
            "id": _emit("regime", "regime_state", title, body,
                         min(1.0, 0.4 + stability * 0.5),
                         {"regime": r1["regime"], "flips_20": flips})}


def strategy_evaluator() -> dict:
    """Rank strategies by last-30-days realized PnL across paper trades + recent backtests."""
    now = int(time.time())
    since = now - 30 * 86400
    with session_scope() as s:
        trades = s.query(Trade).filter(Trade.ts >= since,
                                         Trade.reason.like("close%")).all()
        bts = s.query(BacktestRun).order_by(BacktestRun.id.desc()).limit(50).all()
    by_strategy_pnl: dict[str, float] = {}
    by_strategy_n: dict[str, int] = {}
    for t in trades:
        by_strategy_pnl[t.strategy] = by_strategy_pnl.get(t.strategy, 0.0) + (t.pnl or 0.0)
        by_strategy_n[t.strategy] = by_strategy_n.get(t.strategy, 0) + 1
    by_strategy_sharpe: dict[str, float] = {}
    for b in bts:
        by_strategy_sharpe.setdefault(b.strategy_name, []).append(b.sharpe)
    sharpe_summary = {k: float(np.mean(v)) for k, v in by_strategy_sharpe.items()}
    title = "strategy evaluator"
    body = (f"Recent paper PnL: {by_strategy_pnl}. "
            f"Trade counts: {by_strategy_n}. "
            f"Avg backtest sharpe: {sharpe_summary}.")
    score = 0.5
    return {"agent": "strategy_evaluator", "findings": 1,
            "id": _emit("strategy_evaluator", "ranking", title, body, score,
                         {"paper_pnl": by_strategy_pnl,
                          "trade_n": by_strategy_n,
                          "backtest_avg_sharpe": sharpe_summary})}


def risk_auditor() -> dict:
    """Surface frequent risk blocks; warn if any rule fires too often."""
    now = int(time.time())
    since = now - 24 * 3600
    with session_scope() as s:
        rows = s.query(RiskBlock).filter(RiskBlock.ts >= since).all()
    if not rows:
        return {"agent": "risk_auditor", "findings": 0, "note": "no blocks in 24h"}
    by_rule = Counter(r.rule for r in rows)
    title = f"risk audit: {len(rows)} blocks in 24h"
    body = f"By rule: {by_rule.most_common()}."
    # High block volume from one rule = warning
    worst = by_rule.most_common(1)[0]
    score = min(1.0, 0.4 + worst[1] / 50.0)
    return {"agent": "risk_auditor", "findings": 1,
            "id": _emit("risk_auditor", "audit", title, body, score,
                         {"by_rule": dict(by_rule)})}


def edge_curator() -> dict:
    """Summarize accepted edges and warn about clusters of rejected nearby siblings."""
    with session_scope() as s:
        accepted = s.query(EdgeCandidate).filter(EdgeCandidate.accepted.is_(True)).order_by(EdgeCandidate.id.desc()).limit(20).all()
        total = s.query(EdgeCandidate).count()
    if total == 0:
        return {"agent": "edge_curator", "findings": 0, "note": "no edges tested"}
    summary = [{"name": e.name, "p_adj": e.p_value_adj,
                "effect": e.effect_size, "stability": e.stability}
               for e in accepted]
    title = f"edges: {len(accepted)} accepted of {total} tested"
    score = 0.5 + min(0.5, len(accepted) / 20.0)
    return {"agent": "edge_curator", "findings": 1,
            "id": _emit("edge_curator", "summary", title,
                         f"Accepted edges: {summary[:5]}", score,
                         {"accepted_count": len(accepted), "total": total,
                          "top": summary[:5]})}


def micro_agent() -> dict:
    with session_scope() as s:
        rows = s.query(MicroSignal).order_by(MicroSignal.ts.desc()).limit(20).all()
    if not rows:
        return {"agent": "micro", "findings": 0, "note": "no micro signals yet"}
    by_name: dict[str, float] = {}
    for r in rows:
        by_name.setdefault(r.name, r.zscore if r.zscore is not None else r.value)
    extremes = {k: v for k, v in by_name.items() if v is not None and abs(v) > 2.0}
    title = f"micro: {len(by_name)} signals, extremes={list(extremes.keys())}"
    score = min(1.0, 0.4 + 0.1 * len(extremes))
    return {"agent": "micro", "findings": 1,
            "id": _emit("micro", "micro_state", title,
                         f"Values: {by_name}", score, {"latest": by_name})}


AGENTS: dict[str, Callable[[], dict]] = {
    "news": news_agent,
    "sentiment": sentiment_agent,
    "macro": macro_agent,
    "volatility": volatility_agent,
    "regime": regime_agent,
    "strategy_evaluator": strategy_evaluator,
    "risk_auditor": risk_auditor,
    "edge_curator": edge_curator,
    "micro": micro_agent,
}


def run_agent(name: str) -> dict:
    fn = AGENTS.get(name)
    if fn is None:
        raise KeyError(f"unknown agent: {name}")
    return fn()


def run_all_agents() -> dict:
    out = {}
    for n, fn in AGENTS.items():
        try:
            out[n] = fn()
        except Exception as e:
            out[n] = {"agent": n, "error": str(e)}
    return {"ok": True, "results": out, "ts": int(time.time())}


def recent_findings(*, limit: int = 50, agent: str | None = None,
                     min_score: float = 0.0) -> list[dict]:
    with session_scope() as s:
        q = s.query(ResearchFinding)
        if agent:
            q = q.filter(ResearchFinding.agent == agent)
        if min_score > 0:
            q = q.filter(ResearchFinding.score >= min_score)
        rows = q.order_by(ResearchFinding.id.desc()).limit(limit).all()
        return [{
            "id": r.id, "ts": r.ts, "agent": r.agent, "category": r.category,
            "title": r.title, "body": r.body, "score": r.score,
            "evidence": r.evidence,
        } for r in rows]
