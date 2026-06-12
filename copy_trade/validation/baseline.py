"""Statistical baselines: null hypotheses our scoring must beat."""

from __future__ import annotations

import random
import sqlite3
from typing import Any

from ..backtest.engine import _snapshots_at, _tick_timestamps, _vault_equity_at


def equal_weight_top_n(conn: sqlite3.Connection, n: int = 10,
                       initial_capital: float = 1000.0,
                       rebalance_hours: int = 24) -> dict:
    """At each tick, equal-weight top N by raw aum (proxy for popularity).

    Returns same shape as run_backtest's BacktestResult dict.
    """
    ticks = _tick_timestamps(conn, rebalance_hours)
    equity = initial_capital
    holdings: dict[str, float] = {}
    entry: dict[str, float] = {}
    series = [initial_capital]
    for ts in ticks:
        for uid, alloc in list(holdings.items()):
            ce = _vault_equity_at(conn, uid, ts)
            be = entry.get(uid)
            if ce and be:
                equity += alloc * ((ce / be) - 1.0)
                entry[uid] = ce
        snaps = _snapshots_at(conn, ts)
        snaps.sort(key=lambda s: float(s.get("aum_usdt") or 0), reverse=True)
        chosen = [s["master_uid"] for s in snaps[:n]]
        alloc_each = equity / max(1, len(chosen))
        new_holdings: dict[str, float] = {}
        for uid in chosen:
            new_holdings[uid] = alloc_each
            if uid not in entry:
                e = _vault_equity_at(conn, uid, ts)
                if e:
                    entry[uid] = e
        for uid in list(entry):
            if uid not in new_holdings:
                entry.pop(uid, None)
        holdings = new_holdings
        series.append(equity)
    return _summarize(series, initial_capital, len(ticks))


def random_picks(conn: sqlite3.Connection, n: int = 5, seed: int = 42,
                  initial_capital: float = 1000.0,
                  rebalance_hours: int = 24) -> dict:
    rng = random.Random(seed)
    ticks = _tick_timestamps(conn, rebalance_hours)
    equity = initial_capital
    holdings: dict[str, float] = {}
    entry: dict[str, float] = {}
    series = [initial_capital]
    for ts in ticks:
        for uid, alloc in list(holdings.items()):
            ce = _vault_equity_at(conn, uid, ts)
            be = entry.get(uid)
            if ce and be:
                equity += alloc * ((ce / be) - 1.0)
                entry[uid] = ce
        snaps = _snapshots_at(conn, ts)
        chosen_idx = rng.sample(range(len(snaps)), min(n, len(snaps))) if snaps else []
        chosen = [snaps[i]["master_uid"] for i in chosen_idx]
        alloc_each = equity / max(1, len(chosen))
        new_holdings = {uid: alloc_each for uid in chosen}
        for uid in chosen:
            if uid not in entry:
                e = _vault_equity_at(conn, uid, ts)
                if e:
                    entry[uid] = e
        for uid in list(entry):
            if uid not in new_holdings:
                entry.pop(uid, None)
        holdings = new_holdings
        series.append(equity)
    return _summarize(series, initial_capital, len(ticks))


def _summarize(series: list[float], initial: float, n_ticks: int) -> dict:
    import math
    final = series[-1] if series else initial
    rets = [(series[i] - series[i - 1]) / series[i - 1]
            for i in range(1, len(series)) if series[i - 1] > 0]
    sharpe = None
    if len(rets) >= 2:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        std = math.sqrt(var)
        if std > 1e-12:
            sharpe = (mean / std) * math.sqrt(365)
    peak = initial; worst = 0.0
    for v in series:
        peak = max(peak, v)
        if peak > 0:
            worst = min(worst, (v - peak) / peak)
    return {"final_equity": final,
            "total_return": (final - initial) / initial,
            "sharpe": sharpe, "max_drawdown": worst, "n_ticks": n_ticks}
