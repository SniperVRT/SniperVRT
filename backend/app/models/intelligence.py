"""Phase 2 models: long-term memory, intelligence, edges, research.

These complement (not replace) the original models in all_models.py.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Index, Integer, JSON, String, Text,
)

from backend.app.core.db import Base


class NewsItem(Base):
    __tablename__ = "news_items"
    id = Column(Integer, primary_key=True)
    ts = Column(Integer, nullable=False, index=True)        # event time
    fetched_at = Column(Integer, nullable=False)
    source = Column(String(64), nullable=False, index=True)
    topic = Column(String(32), nullable=False, index=True)  # btc/macro/etf/regulation/...
    headline = Column(Text, nullable=False)
    summary = Column(Text, nullable=False, default="")
    url = Column(Text, nullable=False, default="")
    sentiment = Column(Float, nullable=False, default=0.0)  # -1..+1
    importance = Column(Float, nullable=False, default=0.0)  # 0..1
    market_reaction_1h = Column(Float, nullable=True)        # log-return after 1h
    market_reaction_24h = Column(Float, nullable=True)
    keywords = Column(JSON, nullable=False, default=list)
    __table_args__ = (Index("ix_news_ts_topic", "ts", "topic"),)


class SentimentSnapshot(Base):
    __tablename__ = "sentiment_snapshots"
    id = Column(Integer, primary_key=True)
    ts = Column(Integer, nullable=False, index=True)
    source = Column(String(32), nullable=False, index=True)  # fear_greed / synthetic / etc.
    score = Column(Float, nullable=False)                    # -1..+1 (or 0..100 for FG)
    components = Column(JSON, nullable=False, default=dict)
    notes = Column(Text, nullable=False, default="")


class MicroSignal(Base):
    """Funding, OI, liquidations, volume z-scores, volatility, order-book imbalance."""
    __tablename__ = "micro_signals"
    id = Column(Integer, primary_key=True)
    ts = Column(Integer, nullable=False, index=True)
    name = Column(String(48), nullable=False, index=True)
    value = Column(Float, nullable=False)
    zscore = Column(Float, nullable=True)
    meta = Column(JSON, nullable=False, default=dict)


class RegimeSnapshot(Base):
    """Persisted regime classifications so we can correlate later."""
    __tablename__ = "regime_snapshots"
    id = Column(Integer, primary_key=True)
    ts = Column(Integer, nullable=False, index=True)
    regime = Column(String(32), nullable=False, index=True)
    confidence = Column(Float, nullable=False, default=0.0)
    features = Column(JSON, nullable=False, default=dict)


class EdgeCandidate(Base):
    """A statistically validated (or rejected) predictive relationship."""
    __tablename__ = "edge_candidates"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    name = Column(String(96), nullable=False, index=True)
    feature = Column(String(64), nullable=False)
    target = Column(String(32), nullable=False)              # e.g. ret_1h, ret_24h
    sample_size = Column(Integer, nullable=False)
    effect_size = Column(Float, nullable=False)              # mean conditional return
    base_rate = Column(Float, nullable=False)                # unconditional mean
    t_stat = Column(Float, nullable=False)
    p_value = Column(Float, nullable=False)
    p_value_adj = Column(Float, nullable=False)              # BH-corrected
    stability = Column(Float, nullable=False, default=0.0)   # 0..1, oos vs full
    accepted = Column(Boolean, nullable=False, default=False, index=True)
    rejection_reason = Column(String(128), nullable=False, default="")
    details = Column(JSON, nullable=False, default=dict)


class ResearchFinding(Base):
    """A persisted outcome from an autonomous research agent."""
    __tablename__ = "research_findings"
    id = Column(Integer, primary_key=True)
    ts = Column(Integer, nullable=False, index=True)
    agent = Column(String(48), nullable=False, index=True)
    category = Column(String(32), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    score = Column(Float, nullable=False, default=0.0)
    evidence = Column(JSON, nullable=False, default=dict)


class MemoryEntry(Base):
    """Generic structured memory: failed configs, dangerous regimes, anomalies, etc."""
    __tablename__ = "memory_entries"
    id = Column(Integer, primary_key=True)
    ts = Column(Integer, nullable=False, index=True)
    kind = Column(String(32), nullable=False, index=True)   # failure / success / anomaly / warning
    subject = Column(String(96), nullable=False, index=True)  # what it's about
    summary = Column(Text, nullable=False)
    weight = Column(Float, nullable=False, default=1.0)     # importance / decay weight
    evidence = Column(JSON, nullable=False, default=dict)


class StrategyVersion(Base):
    """Every strategy parameter change is a new version, with the reason."""
    __tablename__ = "strategy_versions"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    strategy_name = Column(String(64), nullable=False, index=True)
    strategy_type = Column(String(32), nullable=False)
    params = Column(JSON, nullable=False, default=dict)
    parent_id = Column(Integer, nullable=True)
    reason = Column(Text, nullable=False, default="")
    approved = Column(Boolean, nullable=False, default=False)
    backtest_run_id = Column(Integer, nullable=True)


class WalkForwardRun(Base):
    """Out-of-sample stability evaluation across rolling windows."""
    __tablename__ = "walk_forward_runs"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    strategy_type = Column(String(32), nullable=False, index=True)
    params = Column(JSON, nullable=False, default=dict)
    window_size = Column(Integer, nullable=False)
    step_size = Column(Integer, nullable=False)
    folds = Column(JSON, nullable=False, default=list)       # per-window metrics
    aggregate = Column(JSON, nullable=False, default=dict)
    stability = Column(Float, nullable=False, default=0.0)
    accepted = Column(Boolean, nullable=False, default=False)


class MonteCarloRun(Base):
    """Bootstrap trade-resample confidence intervals."""
    __tablename__ = "monte_carlo_runs"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    source_run_id = Column(Integer, nullable=True)           # backtest_run id
    n_samples = Column(Integer, nullable=False, default=2000)
    metrics = Column(JSON, nullable=False, default=dict)     # ci on sharpe/dd/ret


class DecisionTrace(Base):
    """A single explainable record per non-trivial decision (paper open/close, block,
    governance verdict). Lets the UI explain WHY anything happened."""
    __tablename__ = "decision_traces"
    id = Column(Integer, primary_key=True)
    ts = Column(Integer, nullable=False, index=True)
    kind = Column(String(32), nullable=False, index=True)    # open / close / block / governance / edge_accept
    subject = Column(String(96), nullable=False)
    reason_chain = Column(JSON, nullable=False, default=list)
    inputs = Column(JSON, nullable=False, default=dict)
    outputs = Column(JSON, nullable=False, default=dict)
