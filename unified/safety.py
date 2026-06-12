"""Cross-system safety cascade."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .db import connect, copytrade_db_path, kalshi_db_path, utc_now_iso

DEFAULT_DRAWDOWN_LIMIT = 0.15


def cross_system_safety_check(unified_conn: sqlite3.Connection | None = None,
                               max_drawdown_pct: float = DEFAULT_DRAWDOWN_LIMIT
                               ) -> dict:
    unified_conn = unified_conn or connect()
    row = unified_conn.execute(
        "SELECT total_drawdown_pct FROM unified_snapshots "
        "ORDER BY captured_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return {"engaged": False, "drawdown": None}
    dd = float(row["total_drawdown_pct"] or 0)
    if dd < max_drawdown_pct:
        return {"engaged": False, "drawdown": dd}

    detail = f"unified_drawdown={dd:.1%}>={max_drawdown_pct:.1%}"
    unified_conn.execute(
        "INSERT INTO unified_safety_events (event_at, event_type, detail) "
        "VALUES (?, 'cross_system_drawdown_stop', ?)",
        (utc_now_iso(), detail),
    )
    # Cascade into kalshi
    kp = kalshi_db_path()
    if kp.exists():
        try:
            kc = sqlite3.connect(kp)
            kc.execute(
                "INSERT INTO governance_locks (name, reason, engaged_at) "
                "VALUES ('emergency', ?, ?)",
                (detail, utc_now_iso()),
            )
            kc.commit()
            kc.close()
        except Exception:  # noqa: BLE001
            pass
    # Cascade into copy_trade
    cp = copytrade_db_path()
    if cp.exists():
        try:
            cc = sqlite3.connect(cp)
            cc.execute(
                "INSERT INTO safety_events (event_at, event_type, detail) "
                "VALUES (?, 'drawdown_stop', ?)",
                (utc_now_iso(), detail),
            )
            cc.commit()
            cc.close()
        except Exception:  # noqa: BLE001
            pass
    return {"engaged": True, "drawdown": dd, "detail": detail}
