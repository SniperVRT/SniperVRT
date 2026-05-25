"""All ORM models for the trading platform."""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Text, ForeignKey, UniqueConstraint, Index, JSON
)
from sqlalchemy.orm import relationship

from backend.app.core.db import Base


class Candle(Base):
    __tablename__ = "candles"
    id = Column(Integer, primary_key=True)
    exchange = Column(String(32), nullable=False, index=True)
    symbol = Column(String(32), nullable=False, index=True)
    timeframe = Column(String(8), nullable=False, index=True)
    ts = Column(Integer, nullable=False, index=True)  # epoch seconds, candle open
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False, default=0.0)
    __table_args__ = (
        UniqueConstraint("exchange", "symbol", "timeframe", "ts", name="uq_candle"),
        Index("ix_candle_lookup", "exchange", "symbol", "timeframe", "ts"),
    )


class StrategyConfig(Base):
    __tablename__ = "strategy_configs"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False, unique=True)
    strategy_type = Column(String(32), nullable=False)
    params = Column(JSON, nullable=False, default=dict)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id = Column(Integer, primary_key=True)
    strategy_name = Column(String(64), nullable=False, index=True)
    strategy_type = Column(String(32), nullable=False)
    params = Column(JSON, nullable=False, default=dict)
    symbol = Column(String(32), nullable=False)
    timeframe = Column(String(8), nullable=False)
    start_ts = Column(Integer, nullable=False)
    end_ts = Column(Integer, nullable=False)
    starting_equity = Column(Float, nullable=False)
    final_equity = Column(Float, nullable=False)
    total_return_pct = Column(Float, nullable=False)
    sharpe = Column(Float, nullable=False, default=0.0)
    max_drawdown_pct = Column(Float, nullable=False, default=0.0)
    win_rate = Column(Float, nullable=False, default=0.0)
    profit_factor = Column(Float, nullable=False, default=0.0)
    num_trades = Column(Integer, nullable=False, default=0)
    avg_trade_pct = Column(Float, nullable=False, default=0.0)
    expectancy = Column(Float, nullable=False, default=0.0)
    metrics = Column(JSON, nullable=False, default=dict)
    equity_curve = Column(JSON, nullable=False, default=list)
    trades_log = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)


class PaperAccount(Base):
    __tablename__ = "paper_accounts"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False, unique=True, default="default")
    equity = Column(Float, nullable=False)
    cash = Column(Float, nullable=False)
    starting_equity = Column(Float, nullable=False)
    peak_equity = Column(Float, nullable=False)
    realized_pnl = Column(Float, nullable=False, default=0.0)
    fees_paid = Column(Float, nullable=False, default=0.0)
    daily_pnl = Column(Float, nullable=False, default=0.0)
    daily_anchor_equity = Column(Float, nullable=False)
    last_day_bucket = Column(String(16), nullable=False, default="")
    halted = Column(Boolean, nullable=False, default=False)
    halt_reason = Column(String(255), nullable=False, default="")
    consecutive_losses = Column(Integer, nullable=False, default=0)
    cooldown_remaining = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Position(Base):
    __tablename__ = "positions"
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("paper_accounts.id"), nullable=False, index=True)
    symbol = Column(String(32), nullable=False)
    side = Column(String(8), nullable=False)  # long / short
    size = Column(Float, nullable=False)       # base units (BTC)
    entry_price = Column(Float, nullable=False)
    stop_price = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    opened_at = Column(Integer, nullable=False)
    closed_at = Column(Integer, nullable=True)
    exit_price = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)
    pnl_pct = Column(Float, nullable=True)
    strategy = Column(String(64), nullable=False, default="")
    is_open = Column(Boolean, nullable=False, default=True, index=True)


class Trade(Base):
    __tablename__ = "trades"
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("paper_accounts.id"), nullable=False, index=True)
    position_id = Column(Integer, ForeignKey("positions.id"), nullable=True)
    ts = Column(Integer, nullable=False, index=True)
    symbol = Column(String(32), nullable=False)
    side = Column(String(8), nullable=False)  # buy / sell
    price = Column(Float, nullable=False)
    requested_price = Column(Float, nullable=False)
    size = Column(Float, nullable=False)
    fee = Column(Float, nullable=False, default=0.0)
    slippage = Column(Float, nullable=False, default=0.0)
    pnl = Column(Float, nullable=False, default=0.0)
    strategy = Column(String(64), nullable=False, default="")
    reason = Column(String(255), nullable=False, default="")
    mode = Column(String(8), nullable=False, default="paper")  # paper or live


class EventLog(Base):
    __tablename__ = "event_log"
    id = Column(Integer, primary_key=True)
    ts = Column(Integer, nullable=False, index=True)
    level = Column(String(16), nullable=False, default="info")
    category = Column(String(32), nullable=False, index=True)
    message = Column(Text, nullable=False)
    payload = Column(JSON, nullable=True)


class RiskBlock(Base):
    __tablename__ = "risk_blocks"
    id = Column(Integer, primary_key=True)
    ts = Column(Integer, nullable=False, index=True)
    rule = Column(String(64), nullable=False)
    detail = Column(Text, nullable=False)
    strategy = Column(String(64), nullable=False, default="")
    payload = Column(JSON, nullable=True)


class LearningRun(Base):
    __tablename__ = "learning_runs"
    id = Column(Integer, primary_key=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)
    config = Column(JSON, nullable=False, default=dict)
    summary = Column(JSON, nullable=True)
    best_strategy = Column(String(64), nullable=True)
    best_sharpe = Column(Float, nullable=True)
    status = Column(String(16), nullable=False, default="running")


class GovernanceDecision(Base):
    __tablename__ = "governance_decisions"
    id = Column(Integer, primary_key=True)
    ts = Column(Integer, nullable=False, index=True)
    category = Column(String(32), nullable=False)
    decision = Column(String(32), nullable=False)
    rationale = Column(Text, nullable=False)
    live_readiness_score = Column(Float, nullable=False, default=0.0)
    payload = Column(JSON, nullable=True)
