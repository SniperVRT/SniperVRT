"""Historical 1-day VaR and CVaR at 95% confidence."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from ..config import CopyTradeSettings, get_settings


def equity_series(conn: sqlite3.Connection, days: int = 30) -> list[float]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT total_capital + realized_pnl + unrealized_pnl AS equity "
        "FROM portfolio_snapshots WHERE captured_at>=? ORDER BY captured_at",
        (cutoff,),
    ).fetchall()
    return [float(r["equity"]) for r in rows]


def daily_returns(equity: list[float]) -> list[float]:
    if len(equity) < 2:
        return []
    return [
        (equity[i] - equity[i - 1]) / equity[i - 1]
        for i in range(1, len(equity)) if equity[i - 1] > 0
    ]


def var_cvar_95(returns: list[float], capital: float) -> tuple[float | None, float | None]:
    if len(returns) < 20:
        return None, None
    sorted_r = sorted(returns)
    # 95% VaR is the 5th percentile loss
    idx = max(0, int(len(sorted_r) * 0.05))
    var_pct = sorted_r[idx]
    tail = sorted_r[: idx + 1]
    cvar_pct = sum(tail) / len(tail)
    return abs(var_pct) * capital, abs(cvar_pct) * capital


def compute(conn: sqlite3.Connection,
            settings: CopyTradeSettings | None = None) -> dict:
    settings = settings or get_settings()
    s = equity_series(conn, days=30)
    r = daily_returns(s)
    var95, cvar95 = var_cvar_95(r, settings.total_capital_usdt)
    return {"n_days": len(r), "var_95_usdt": var95, "cvar_95_usdt": cvar95}
