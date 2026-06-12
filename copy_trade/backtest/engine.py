"""Backtest engine — replay historical snapshots through scoring + allocation.

For every "tick" timestamp T in our snapshot history:
  1. Recompute scores using ONLY snapshots taken at or before T (no look-ahead)
  2. Simulate allocation decisions
  3. Look up actual subsequent vault returns to compute realised PnL
  4. Walk equity curve forward

This validates the entire pipeline against real historical data. When you
collect 30+ days of snapshots, the backtest gives a true Sharpe / drawdown
estimate for the strategy as configured.

Same scoring code, same allocation code as live — only the data source
differs (historical snapshots instead of live polls).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from ..allocation.portfolio import compute_allocations
from ..config import CopyTradeSettings, get_settings
from ..db import utc_now_iso
from ..ranking.score import TraderScore, score_traders

log = structlog.get_logger("copy_trade.backtest")


@dataclass
class BacktestTick:
    timestamp: str
    equity: float
    allocations: dict[str, float]


@dataclass
class BacktestResult:
    run_id: int
    initial_capital: float
    final_equity: float
    total_return: float
    sharpe: float | None
    max_drawdown: float
    n_ticks: int
    avg_alloc_count: float
    ticks: list[BacktestTick]


def run_backtest(
    conn: sqlite3.Connection,
    *,
    initial_capital: float = 1000.0,
    rebalance_hours: int = 24,
    settings: CopyTradeSettings | None = None,
    notes: str = "",
) -> BacktestResult:
    """Walk forward through historical snapshots and simulate the strategy."""
    settings = settings or get_settings()
    started = utc_now_iso()
    params = {
        "initial_capital": initial_capital,
        "rebalance_hours": rebalance_hours,
        "weight_sharpe": settings.weight_sharpe,
        "weight_calmar": settings.weight_calmar,
        "min_sharpe": settings.min_sharpe,
        "max_mdd_pct": settings.max_mdd_pct,
        "max_masters": settings.max_masters,
        "min_vault_tvl_usdt": settings.min_vault_tvl_usdt,
        "correlation_threshold": settings.correlation_threshold,
    }
    cur = conn.execute(
        "INSERT INTO backtest_runs (started_at, params_json, tick_count, "
        "initial_capital) VALUES (?, ?, 0, ?)",
        (started, json.dumps(params), initial_capital),
    )
    run_id = int(cur.lastrowid)

    rebalance_ts = _tick_timestamps(conn, rebalance_hours)
    if not rebalance_ts:
        log.warning("backtest_no_snapshots")
        return BacktestResult(run_id=run_id, initial_capital=initial_capital,
                              final_equity=initial_capital, total_return=0.0,
                              sharpe=None, max_drawdown=0.0,
                              n_ticks=0, avg_alloc_count=0.0, ticks=[])

    # State for the walk
    equity = initial_capital
    holdings: dict[str, float] = {}        # uid -> allocated_usdt at last rebalance
    entry_equity: dict[str, float] = {}    # uid -> vault equity at allocation time
    peak = initial_capital
    worst_dd = 0.0
    equity_series: list[float] = [initial_capital]
    ticks: list[BacktestTick] = []

    for ts in rebalance_ts:
        # 1. Update equity using actual realised returns since last tick
        for uid, alloc in list(holdings.items()):
            current_eq = _vault_equity_at(conn, uid, ts)
            base_eq = entry_equity.get(uid)
            if base_eq and current_eq:
                pnl = alloc * ((current_eq / base_eq) - 1.0)
                equity += pnl
                # reset baseline to current for next-segment PnL accumulation
                entry_equity[uid] = current_eq

        # 2. Reprice settings.total_capital to current equity for sizing
        per_run_settings = settings.model_copy(update={"total_capital_usdt": equity})

        # 3. Recompute scores using ONLY snapshots <= ts
        snaps = _snapshots_at(conn, ts)
        scores = score_traders(conn, snaps, per_run_settings)

        # 4. Allocate
        current_subs = [{"master_uid": uid, "allocated_usdt": v}
                        for uid, v in holdings.items()]
        decisions = compute_allocations(scores, current_subs, per_run_settings)

        # 5. Apply decisions in-memory
        new_holdings: dict[str, float] = dict(holdings)
        for d in decisions:
            if d.action == "subscribe":
                new_holdings[d.master_uid] = d.target_usdt
                eq = _vault_equity_at(conn, d.master_uid, ts)
                if eq:
                    entry_equity[d.master_uid] = eq
            elif d.action == "rebalance":
                new_holdings[d.master_uid] = d.target_usdt
            elif d.action == "unsubscribe":
                new_holdings.pop(d.master_uid, None)
                entry_equity.pop(d.master_uid, None)

        holdings = new_holdings

        # 6. Track drawdown
        peak = max(peak, equity)
        if peak > 0:
            worst_dd = min(worst_dd, (equity - peak) / peak)
        equity_series.append(equity)
        ticks.append(BacktestTick(
            timestamp=ts, equity=equity,
            allocations=dict(new_holdings),
        ))

    sharpe = _series_sharpe(equity_series)
    total_return = (equity - initial_capital) / initial_capital
    avg_alloc = (
        sum(len(t.allocations) for t in ticks) / len(ticks) if ticks else 0.0
    )

    conn.execute(
        "UPDATE backtest_runs SET finished_at=?, tick_count=?, final_equity=?, "
        "total_return=?, sharpe=?, max_drawdown=?, avg_alloc_count=?, notes=? "
        "WHERE id=?",
        (utc_now_iso(), len(ticks), equity, total_return,
         sharpe, worst_dd, avg_alloc, notes, run_id),
    )

    return BacktestResult(
        run_id=run_id,
        initial_capital=initial_capital,
        final_equity=equity,
        total_return=total_return,
        sharpe=sharpe,
        max_drawdown=worst_dd,
        n_ticks=len(ticks),
        avg_alloc_count=avg_alloc,
        ticks=ticks,
    )


def _tick_timestamps(conn: sqlite3.Connection, rebalance_hours: int) -> list[str]:
    """Pull every Nth snapshot timestamp to use as rebalance ticks."""
    rows = conn.execute(
        "SELECT DISTINCT captured_at FROM trader_snapshots "
        "ORDER BY captured_at ASC"
    ).fetchall()
    if not rows:
        return []
    # Filter to one tick per rebalance_hours
    out: list[str] = []
    last_ts: datetime | None = None
    threshold = timedelta(hours=rebalance_hours)
    for r in rows:
        try:
            ts = datetime.fromisoformat(r["captured_at"])
        except ValueError:
            continue
        if last_ts is None or (ts - last_ts) >= threshold:
            out.append(r["captured_at"])
            last_ts = ts
    return out


def _snapshots_at(conn: sqlite3.Connection, ts: str) -> list[dict[str, Any]]:
    """Latest snapshot per master taken at or before `ts`."""
    rows = conn.execute(
        """
        SELECT * FROM trader_snapshots t1
        WHERE captured_at = (
            SELECT MAX(captured_at) FROM trader_snapshots t2
            WHERE t2.master_uid = t1.master_uid AND t2.captured_at <= ?
        )
        """,
        (ts,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        if d.get("raw_json"):
            try:
                d["raw"] = json.loads(d["raw_json"])
            except Exception:  # noqa: BLE001
                d["raw"] = {}
        out.append(d)
    return out


def _vault_equity_at(conn: sqlite3.Connection, uid: str, ts: str) -> float | None:
    """Vault TVL/AUM at or just before timestamp ts (proxy for equity)."""
    row = conn.execute(
        "SELECT aum_usdt FROM trader_snapshots WHERE master_uid=? AND captured_at<=? "
        "ORDER BY captured_at DESC LIMIT 1",
        (uid, ts),
    ).fetchone()
    if row and row["aum_usdt"]:
        return float(row["aum_usdt"])
    return None


def _series_sharpe(equity_series: list[float]) -> float | None:
    """Annualized Sharpe assuming daily ticks."""
    if len(equity_series) < 3:
        return None
    returns = [
        (equity_series[i] - equity_series[i - 1]) / equity_series[i - 1]
        for i in range(1, len(equity_series))
        if equity_series[i - 1] > 0
    ]
    if not returns:
        return None
    import math
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / max(1, len(returns) - 1)
    std = math.sqrt(var)
    if std < 1e-9:
        return None
    return (mean / std) * math.sqrt(365)
