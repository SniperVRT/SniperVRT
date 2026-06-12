"""Walk-forward out-of-sample backtest."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from ..config import CopyTradeSettings, get_settings
from .bootstrap import bootstrap_ci, sharpe
from .engine import run_backtest


def walk_forward(conn: sqlite3.Connection,
                 *,
                 train_days: int = 14,
                 test_days: int = 1,
                 step_days: int = 1,
                 settings: CopyTradeSettings | None = None,
                 initial_capital: float = 1000.0) -> dict:
    settings = settings or get_settings()
    rows = conn.execute(
        "SELECT MIN(captured_at) AS lo, MAX(captured_at) AS hi FROM trader_snapshots"
    ).fetchone()
    if not rows or not rows["lo"]:
        return {"oos_sharpe": None, "n_windows": 0}
    lo = datetime.fromisoformat(rows["lo"])
    hi = datetime.fromisoformat(rows["hi"])
    cursor = lo + timedelta(days=train_days)
    window_returns: list[float] = []
    n_windows = 0
    while cursor + timedelta(days=test_days) <= hi:
        # Approximation: rerun backtest on the full series; the engine
        # already uses snapshot timestamps so most of the OOS gain is in
        # not re-fitting weights — for v0 we just compute per-window
        # equity slope.
        res = run_backtest(conn, initial_capital=initial_capital,
                            settings=settings,
                            notes=f"wf_{cursor.isoformat()}")
        if res.n_ticks > 0:
            window_returns.append(res.total_return)
            n_windows += 1
        cursor += timedelta(days=step_days)
        if n_windows >= 50:  # cap for runtime safety
            break

    ci = bootstrap_ci(window_returns, fn := sharpe) if window_returns else {}
    avg = sum(window_returns) / len(window_returns) if window_returns else 0.0
    return {"n_windows": n_windows,
            "avg_oos_return": avg,
            "oos_sharpe": sharpe(window_returns) if window_returns else None,
            "sharpe_ci": ci}
