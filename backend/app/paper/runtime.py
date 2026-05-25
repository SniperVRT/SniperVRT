"""Paper-trading runtime: a long-lived async loop that periodically ticks the strategy
through the risk engine and persists every state change. Crash-safe; resumes from DB.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from typing import Optional

from backend.app.core.config import get_config
from backend.app.core.db import session_scope
from backend.app.data import ensure_dataset, load_candles, data_quality
from backend.app.execution.simulator import simulate_fill
from backend.app.models import (
    PaperAccount, Position, Trade, EventLog, StrategyConfig,
)
from backend.app.risk.engine import RiskEngine
from backend.app.strategies import build_strategy, detect_regime

log = logging.getLogger(__name__)


def _now() -> int:
    return int(time.time())


def _day_bucket() -> str:
    return dt.datetime.utcnow().strftime("%Y-%m-%d")


def _log_event(level: str, category: str, message: str, payload: dict | None = None) -> None:
    with session_scope() as s:
        s.add(EventLog(ts=_now(), level=level, category=category,
                       message=message, payload=payload or {}))


class PaperRuntime:
    def __init__(self) -> None:
        self.cfg = get_config()
        self.risk = RiskEngine()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._stop_event = threading.Event()
        self._active_strategy_name: str = "ensemble"
        self._lock = threading.Lock()

    # ------------------------ account helpers ------------------------
    def _ensure_account(self) -> PaperAccount:
        with session_scope() as s:
            acc = s.query(PaperAccount).filter_by(name="default").one_or_none()
            if acc is None:
                acc = PaperAccount(
                    name="default",
                    equity=self.cfg.paper.starting_equity,
                    cash=self.cfg.paper.starting_equity,
                    starting_equity=self.cfg.paper.starting_equity,
                    peak_equity=self.cfg.paper.starting_equity,
                    daily_anchor_equity=self.cfg.paper.starting_equity,
                    last_day_bucket=_day_bucket(),
                )
                s.add(acc)
                s.flush()
            # Roll daily anchor
            today = _day_bucket()
            if acc.last_day_bucket != today:
                acc.last_day_bucket = today
                acc.daily_anchor_equity = acc.equity
                acc.daily_pnl = 0.0
            s.refresh(acc)
            # Detach: build a dict copy and re-query inside callers
            return acc

    def _get_account(self, s) -> PaperAccount:
        acc = s.query(PaperAccount).filter_by(name="default").one_or_none()
        if acc is None:
            acc = PaperAccount(
                name="default",
                equity=self.cfg.paper.starting_equity,
                cash=self.cfg.paper.starting_equity,
                starting_equity=self.cfg.paper.starting_equity,
                peak_equity=self.cfg.paper.starting_equity,
                daily_anchor_equity=self.cfg.paper.starting_equity,
                last_day_bucket=_day_bucket(),
            )
            s.add(acc)
            s.flush()
        today = _day_bucket()
        if acc.last_day_bucket != today:
            acc.last_day_bucket = today
            acc.daily_anchor_equity = acc.equity
            acc.daily_pnl = 0.0
        return acc

    # ------------------------ state queries --------------------------
    def status(self) -> dict:
        with session_scope() as s:
            acc = self._get_account(s)
            open_positions = s.query(Position).filter_by(account_id=acc.id, is_open=True).all()
            df = load_candles(limit=400)
            regime = detect_regime(df) if not df.empty else {"regime": "unknown"}
            last_price = float(df["close"].iloc[-1]) if not df.empty else None
            # Mark-to-market: cash + unrealized PnL of open positions (cash-margin model)
            unrealized = 0.0
            for p in open_positions:
                if last_price:
                    sign = 1 if p.side == "long" else -1
                    unrealized += (last_price - p.entry_price) * p.size * sign
            equity_mtm = acc.cash + unrealized
            return {
                "running": self._running,
                "active_strategy": self._active_strategy_name,
                "equity": round(equity_mtm, 4),
                "cash": round(acc.cash, 4),
                "starting_equity": acc.starting_equity,
                "peak_equity": acc.peak_equity,
                "realized_pnl": round(acc.realized_pnl, 4),
                "unrealized_pnl": round(unrealized, 4),
                "daily_pnl": round(equity_mtm - acc.daily_anchor_equity, 4),
                "daily_anchor_equity": acc.daily_anchor_equity,
                "fees_paid": round(acc.fees_paid, 4),
                "halted": acc.halted,
                "halt_reason": acc.halt_reason,
                "consecutive_losses": acc.consecutive_losses,
                "cooldown_remaining": acc.cooldown_remaining,
                "open_positions": [{
                    "id": p.id, "symbol": p.symbol, "side": p.side, "size": p.size,
                    "entry": p.entry_price, "stop": p.stop_price, "tp": p.take_profit,
                    "opened_at": p.opened_at, "strategy": p.strategy,
                    "unrealized": round(((last_price or p.entry_price) - p.entry_price) * p.size * (1 if p.side == "long" else -1), 4) if last_price else 0.0,
                } for p in open_positions],
                "last_price": last_price,
                "regime": regime,
                "live_mode": False,
            }

    def equity_curve(self, limit: int = 500) -> list[dict]:
        with session_scope() as s:
            acc = self._get_account(s)
            rows = s.query(Trade).filter_by(account_id=acc.id).order_by(Trade.ts.asc()).all()
            equity = acc.starting_equity
            curve = [{"ts": rows[0].ts - 60 if rows else _now(), "equity": acc.starting_equity}]
            for t in rows:
                equity += t.pnl - t.fee
                curve.append({"ts": t.ts, "equity": round(equity, 4)})
            return curve[-limit:]

    # ------------------------ strategy selection ---------------------
    def set_active_strategy(self, name: str) -> None:
        from backend.app.strategies import STRATEGY_REGISTRY
        if name not in STRATEGY_REGISTRY:
            raise KeyError(f"unknown strategy: {name}")
        self._active_strategy_name = name
        _log_event("info", "paper", f"active strategy set to {name}")

    def _build_active_strategy(self):
        # Try to load a saved config; fall back to defaults
        with session_scope() as s:
            cfg = s.query(StrategyConfig).filter_by(name=self._active_strategy_name).one_or_none()
            params = cfg.params if cfg else None
            strategy_type = cfg.strategy_type if cfg else self._active_strategy_name
        return build_strategy(strategy_type, params=params)

    # ------------------------ tick logic -----------------------------
    def tick_once(self) -> dict:
        """Single decision tick: ensure data, evaluate signal, manage position. Returns summary."""
        # Refresh data so we have an up-to-date last candle
        ensure_dataset()
        df = load_candles(limit=400)
        if df.empty:
            return {"ok": False, "reason": "no data"}
        dq = data_quality()
        with session_scope() as s:
            acc = self._get_account(s)
            open_positions = s.query(Position).filter_by(account_id=acc.id, is_open=True).all()
            last_price = float(df["close"].iloc[-1])
            ts = _now()

            # First, manage open positions (stops/take-profits, signal flips)
            strategy = self._build_active_strategy()
            sig = strategy.signal_now(df)
            for p in list(open_positions):
                hit_stop = (p.side == "long" and p.stop_price and last_price <= p.stop_price) or \
                           (p.side == "short" and p.stop_price and last_price >= p.stop_price)
                hit_tp = (p.side == "long" and p.take_profit and last_price >= p.take_profit) or \
                         (p.side == "short" and p.take_profit and last_price <= p.take_profit)
                flipped = sig.side in ("long", "short") and \
                          ((sig.side == "long" and p.side == "short") or (sig.side == "short" and p.side == "long"))
                gone_flat = sig.side == "flat"
                if hit_stop or hit_tp or flipped or gone_flat:
                    reason = "stop" if hit_stop else ("take_profit" if hit_tp else ("signal_flip" if flipped else "signal_flat"))
                    self._close_position(s, acc, p, last_price, ts, reason)
                    open_positions = [op for op in open_positions if op.id != p.id]

            # Cooldown decays each tick
            if acc.cooldown_remaining > 0:
                acc.cooldown_remaining -= 1

            # Risk evaluation for opening
            decision_payload: dict = {
                "signal": sig.side, "confidence": sig.confidence,
                "stop_pct": sig.stop_pct, "tp_pct": sig.take_profit_pct,
                "last_price": last_price,
            }
            allow_check = self.risk.evaluate(
                account=acc, df=df, signal_side=sig.side, confidence=sig.confidence,
                stop_pct=sig.stop_pct, tp_pct=sig.take_profit_pct,
                open_positions=open_positions, data_quality_score=dq.coverage_pct,
                strategy=self._active_strategy_name,
            )
            decision_payload["risk_check"] = allow_check.to_dict()

            if allow_check.allowed and len(open_positions) == 0:
                self._open_position(s, acc, last_price, sig.side, allow_check, ts)
                decision_payload["opened"] = True
            else:
                decision_payload["opened"] = False

            # Apply halt rules persistently
            if "DAILY_LOSS" in ",".join(allow_check.blocks):
                acc.halted = True
                acc.halt_reason = "daily_loss_limit"
            if "MAX_DRAWDOWN" in ",".join(allow_check.blocks):
                acc.halted = True
                acc.halt_reason = "max_drawdown"

            # Mark-to-market equity for snapshot (cash-margin model)
            unrealized = 0.0
            for p in s.query(Position).filter_by(account_id=acc.id, is_open=True).all():
                sign = 1 if p.side == "long" else -1
                unrealized += (last_price - p.entry_price) * p.size * sign
            acc.equity = acc.cash + unrealized
            acc.peak_equity = max(acc.peak_equity, acc.equity)
            acc.daily_pnl = acc.equity - acc.daily_anchor_equity

            _log_event("info", "paper.tick", "tick", decision_payload)
            return {"ok": True, **decision_payload}

    # ------------------------ position helpers ------------------------
    def _open_position(self, s, acc: PaperAccount, last_price: float, side: str,
                       check, ts: int) -> None:
        # Cash-margin: cash holds realized equity. Opening only charges the entry fee.
        side_sym = "buy" if side == "long" else "sell"
        fill = simulate_fill(side_sym, last_price, check.size_base, taker=True)
        notional = fill.fill_price * check.size_base
        # Require enough free equity to cover at least the max-loss budget on this trade.
        max_loss = abs(check.stop_price - fill.fill_price) * check.size_base if check.stop_price else notional * 0.05
        if acc.cash < max_loss + fill.fee:
            return
        acc.cash -= fill.fee
        acc.fees_paid += fill.fee
        p = Position(
            account_id=acc.id, symbol=self.cfg.data.default_symbol,
            side=side, size=check.size_base, entry_price=fill.fill_price,
            stop_price=check.stop_price, take_profit=check.take_profit,
            opened_at=ts, strategy=self._active_strategy_name, is_open=True,
        )
        s.add(p)
        s.flush()
        s.add(Trade(
            account_id=acc.id, position_id=p.id, ts=ts,
            symbol=p.symbol, side=side_sym, price=fill.fill_price,
            requested_price=last_price, size=check.size_base, fee=fill.fee,
            slippage=fill.slippage_cost, pnl=0.0,
            strategy=self._active_strategy_name, reason="open", mode="paper",
        ))

    def _close_position(self, s, acc: PaperAccount, p: Position, last_price: float,
                        ts: int, reason: str) -> None:
        side_sym = "sell" if p.side == "long" else "buy"
        fill = simulate_fill(side_sym, last_price, p.size, taker=True)
        sign = 1 if p.side == "long" else -1
        gross = (fill.fill_price - p.entry_price) * p.size * sign
        net = gross - fill.fee
        acc.cash += net
        acc.fees_paid += fill.fee
        acc.realized_pnl += net
        p.is_open = False
        p.closed_at = ts
        p.exit_price = fill.fill_price
        p.pnl = net
        p.pnl_pct = net / (p.entry_price * p.size) if p.entry_price * p.size > 0 else 0.0
        s.add(Trade(
            account_id=acc.id, position_id=p.id, ts=ts,
            symbol=p.symbol, side=side_sym, price=fill.fill_price,
            requested_price=last_price, size=p.size, fee=fill.fee,
            slippage=fill.slippage_cost, pnl=net,
            strategy=p.strategy, reason=f"close:{reason}", mode="paper",
        ))
        # Loss streak / cooldown
        if net <= 0:
            acc.consecutive_losses += 1
            if acc.consecutive_losses >= self.cfg.risk.consecutive_loss_cooldown:
                acc.cooldown_remaining = self.cfg.risk.consecutive_loss_cooldown
        else:
            acc.consecutive_losses = 0

    # ------------------------ background loop ------------------------
    def _loop(self) -> None:
        self._running = True
        _log_event("info", "paper", "runtime started")
        try:
            while not self._stop_event.is_set():
                try:
                    self.tick_once()
                except Exception as e:
                    log.exception("paper tick failed")
                    try:
                        _log_event("error", "paper", f"tick failed: {e}")
                    except Exception:
                        pass
                # Sleep with cancellation
                self._stop_event.wait(self.cfg.paper.tick_seconds)
        finally:
            self._running = False
            _log_event("info", "paper", "runtime stopped")

    def start(self) -> dict:
        with self._lock:
            if self._running:
                return {"running": True, "already": True}
            self._stop_event = threading.Event()
            self._ensure_account()
            self._thread = threading.Thread(target=self._loop, name="paper-runtime",
                                            daemon=True)
            self._thread.start()
            return {"running": True}

    def stop(self) -> dict:
        with self._lock:
            if not self._running:
                return {"running": False}
            self._stop_event.set()
            if self._thread is not None:
                self._thread.join(timeout=5)
            return {"stopped": True}

    def reset(self) -> dict:
        """Wipe paper account state and start fresh."""
        with self._lock:
            with session_scope() as s:
                acc = s.query(PaperAccount).filter_by(name="default").one_or_none()
                if acc:
                    s.query(Position).filter_by(account_id=acc.id).delete()
                    s.query(Trade).filter_by(account_id=acc.id).delete()
                    s.delete(acc)
            _log_event("info", "paper", "account reset")
            self._ensure_account()
            return {"reset": True}


_runtime: Optional[PaperRuntime] = None


def get_runtime() -> PaperRuntime:
    global _runtime
    if _runtime is None:
        _runtime = PaperRuntime()
    return _runtime
