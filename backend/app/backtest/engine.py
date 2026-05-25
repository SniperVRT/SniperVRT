"""Event-driven-ish backtest engine.

Iterates bar-by-bar, calls the strategy on the closed window (no look-ahead),
applies stops/take-profits intra-bar, and accounts for fees and slippage.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from backend.app.core.config import get_config
from backend.app.execution.simulator import simulate_fill
from backend.app.strategies.base import Strategy


@dataclass
class BacktestResult:
    starting_equity: float
    final_equity: float
    total_return_pct: float
    sharpe: float
    max_drawdown_pct: float
    win_rate: float
    profit_factor: float
    num_trades: int
    avg_trade_pct: float
    expectancy: float
    equity_curve: list[dict] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "starting_equity": self.starting_equity,
            "final_equity": round(self.final_equity, 2),
            "total_return_pct": round(self.total_return_pct, 6),
            "sharpe": round(self.sharpe, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 6),
            "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 4),
            "num_trades": int(self.num_trades),
            "avg_trade_pct": round(self.avg_trade_pct, 6),
            "expectancy": round(self.expectancy, 6),
            "equity_curve": self.equity_curve,
            "trades": self.trades,
            "metrics": self.metrics,
        }


def _sharpe(returns: list[float], periods_per_year: float = 365 * 24) -> float:
    if len(returns) < 2:
        return 0.0
    arr = np.array(returns)
    std = arr.std(ddof=1)
    if std == 0:
        return 0.0
    return float((arr.mean() / std) * math.sqrt(periods_per_year))


def run_backtest(
    strategy: Strategy,
    df: pd.DataFrame,
    starting_equity: float = 10_000.0,
    position_size_pct: float = 0.95,
    confidence_floor: float = 0.0,
    allow_short: bool = True,
) -> BacktestResult:
    if df.empty:
        return BacktestResult(starting_equity, starting_equity, 0, 0, 0, 0, 0, 0, 0, 0, [], [], {})

    sig_df = strategy.generate(df)
    sig_df = sig_df.reindex(df.index).fillna({"signal": 0, "confidence": 0.0,
                                              "stop_pct": 0.02, "tp_pct": 0.05})
    cfg_exec = get_config().execution

    # Cash-margin accounting: `cash` is realized equity. Open positions add unrealized PnL only.
    cash = starting_equity
    peak_equity = starting_equity
    equity = starting_equity
    pos_side = 0          # +1 / -1 / 0
    pos_size = 0.0        # base units (BTC)
    pos_entry = 0.0
    pos_stop = None
    pos_tp = None
    pos_open_ts = 0

    trades: list[dict] = []
    equity_curve: list[dict] = []
    returns: list[float] = []
    last_equity = equity

    def _close_position(close_price: float, ts: int, reason: str) -> None:
        nonlocal equity, cash, pos_side, pos_size, pos_entry, pos_stop, pos_tp, last_equity, peak_equity
        if pos_side == 0 or pos_size == 0:
            return
        side_str = "sell" if pos_side > 0 else "buy"
        fill = simulate_fill(side_str, close_price, pos_size, taker=True)
        gross = (fill.fill_price - pos_entry) * pos_size * pos_side
        net = gross - fill.fee
        cash += net
        equity = cash
        peak_equity = max(peak_equity, equity)
        notional = pos_entry * pos_size
        ret_pct = net / notional if notional > 0 else 0.0
        trades.append({
            "open_ts": pos_open_ts, "close_ts": ts,
            "side": "long" if pos_side > 0 else "short",
            "entry": round(pos_entry, 2), "exit": round(fill.fill_price, 2),
            "size": round(pos_size, 8), "pnl": round(net, 4),
            "ret_pct": round(ret_pct, 6), "reason": reason,
            "fee": round(fill.fee, 4),
        })
        pos_side = 0
        pos_size = 0.0
        pos_entry = 0.0
        pos_stop = None
        pos_tp = None

    for i, (idx, row) in enumerate(df.iterrows()):
        ts = int(row.get("ts", int(idx.timestamp() if hasattr(idx, "timestamp") else 0)))
        close = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])

        # Check stops/take-profits intra-bar before any new signal
        if pos_side > 0:
            if pos_stop is not None and low <= pos_stop:
                _close_position(pos_stop, ts, "stop")
            elif pos_tp is not None and high >= pos_tp:
                _close_position(pos_tp, ts, "take_profit")
        elif pos_side < 0:
            if pos_stop is not None and high >= pos_stop:
                _close_position(pos_stop, ts, "stop")
            elif pos_tp is not None and low <= pos_tp:
                _close_position(pos_tp, ts, "take_profit")

        # Use signal from the just-closed bar (i) — strategies don't peek the future because
        # `generate` uses standard pandas indicators which are causal.
        sig_row = sig_df.iloc[i]
        signal = int(sig_row["signal"])
        conf = float(sig_row.get("confidence", 0.0))
        stop_pct = float(sig_row.get("stop_pct", 0.02))
        tp_pct = float(sig_row.get("tp_pct", 0.05))

        if conf < confidence_floor:
            signal = 0
        if not allow_short and signal < 0:
            signal = 0

        # Manage position: flip if signal reverses; close if it goes flat
        if pos_side != 0 and signal != pos_side:
            _close_position(close, ts, "signal_flip" if signal != 0 else "signal_flat")

        # Open new position
        if pos_side == 0 and signal != 0:
            target_notional = equity * position_size_pct
            size = target_notional / close
            if size * close >= 10:
                side_str = "buy" if signal > 0 else "sell"
                fill = simulate_fill(side_str, close, size, taker=True)
                cash -= fill.fee           # entry fee charged immediately
                pos_side = signal
                pos_size = size
                pos_entry = fill.fill_price
                pos_open_ts = ts
                pos_stop = pos_entry * (1 - stop_pct) if signal > 0 else pos_entry * (1 + stop_pct)
                pos_tp = pos_entry * (1 + tp_pct) if signal > 0 else pos_entry * (1 - tp_pct)

        # Mark-to-market: equity = realized cash + unrealized PnL of any open position
        unrealized = (close - pos_entry) * pos_size * pos_side if pos_side != 0 else 0.0
        equity = cash + unrealized
        peak_equity = max(peak_equity, equity)

        if last_equity > 0:
            returns.append((equity - last_equity) / last_equity)
        last_equity = equity

        equity_curve.append({
            "ts": ts, "equity": round(equity, 4), "price": close,
            "position": pos_side, "drawdown": round((equity - peak_equity) / peak_equity, 6) if peak_equity > 0 else 0.0,
        })

    # Force close at end
    if pos_side != 0:
        _close_position(float(df["close"].iloc[-1]),
                        int(df["ts"].iloc[-1]) if "ts" in df.columns else 0,
                        "end_of_data")
        equity_curve[-1]["equity"] = round(equity, 4)

    # Metrics
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins) if wins else 0.0
    gross_loss = sum(t["pnl"] for t in losses) if losses else 0.0
    win_rate = len(wins) / len(trades) if trades else 0.0
    profit_factor = (gross_win / abs(gross_loss)) if gross_loss < 0 else (gross_win if gross_win > 0 else 0.0)
    avg_trade_pct = float(np.mean([t["ret_pct"] for t in trades])) if trades else 0.0
    expectancy = float(np.mean([t["pnl"] for t in trades])) if trades else 0.0
    sharpe = _sharpe(returns)
    eq_arr = np.array([e["equity"] for e in equity_curve])
    if len(eq_arr) > 0:
        running_max = np.maximum.accumulate(eq_arr)
        dd = (eq_arr - running_max) / running_max
        max_dd = float(dd.min())
    else:
        max_dd = 0.0

    return BacktestResult(
        starting_equity=starting_equity,
        final_equity=float(equity),
        total_return_pct=float((equity - starting_equity) / starting_equity),
        sharpe=sharpe,
        max_drawdown_pct=max_dd,
        win_rate=win_rate,
        profit_factor=float(profit_factor),
        num_trades=len(trades),
        avg_trade_pct=avg_trade_pct,
        expectancy=expectancy,
        equity_curve=equity_curve,
        trades=trades,
        metrics={
            "gross_win": round(gross_win, 4),
            "gross_loss": round(gross_loss, 4),
            "wins": len(wins),
            "losses": len(losses),
        },
    )
