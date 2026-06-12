"""Model versioning so we can compare historical performance across weight changes."""

from __future__ import annotations

import json
import sqlite3

from ..config import CopyTradeSettings, get_settings
from ..db import utc_now_iso


def current_version_string(settings: CopyTradeSettings | None = None) -> str:
    settings = settings or get_settings()
    return (
        f"v-{settings.allocation_method}"
        f"-sharpe{settings.weight_sharpe:.2f}"
        f"-calmar{settings.weight_calmar:.2f}"
        f"-mddmax{settings.max_mdd_pct:.2f}"
    )


def record_version(conn: sqlite3.Connection,
                    settings: CopyTradeSettings | None = None,
                    notes: str = "") -> str:
    settings = settings or get_settings()
    v = current_version_string(settings)
    params = {
        "allocation_method": settings.allocation_method,
        "weight_sharpe": settings.weight_sharpe,
        "weight_calmar": settings.weight_calmar,
        "weight_win_rate": settings.weight_win_rate,
        "weight_profit_factor": settings.weight_profit_factor,
        "weight_consistency": settings.weight_consistency,
        "min_sharpe": settings.min_sharpe,
        "max_mdd_pct": settings.max_mdd_pct,
        "max_masters": settings.max_masters,
        "correlation_threshold": settings.correlation_threshold,
        "kelly_fraction": settings.kelly_fraction,
    }
    conn.execute(
        "INSERT OR IGNORE INTO model_versions (version, params_json, "
        "started_at, notes) VALUES (?,?,?,?)",
        (v, json.dumps(params), utc_now_iso(), notes),
    )
    return v


def list_versions(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM model_versions ORDER BY id DESC"
    ).fetchall()
    return [dict(r) for r in rows]
