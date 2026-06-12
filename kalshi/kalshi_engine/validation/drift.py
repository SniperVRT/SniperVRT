"""Paper-vs-live drift tracking.

Compares expected (paper) execution against actual (live or live-preview)
behavior. Persists per-trade rows in `drift_reports` and offers a
summary aggregator.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..db import utc_now_iso


def compute_drift(
    conn: sqlite3.Connection,
    *,
    expected_cents: float,
    actual_cents: float,
    expected_spread_cents: float | None = None,
    realized_spread_cents: float | None = None,
    ticker: str | None = None,
    execution_delay_ms: float | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    slippage = actual_cents - expected_cents
    conn.execute(
        """
        INSERT INTO drift_reports (
            captured_at, ticker, expected_fill_cents, actual_fill_cents,
            expected_spread_cents, realized_spread_cents,
            slippage_cents, execution_delay_ms, notes
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (utc_now_iso(), ticker, expected_cents, actual_cents,
         expected_spread_cents, realized_spread_cents,
         slippage, execution_delay_ms, notes),
    )
    return {
        "ticker": ticker,
        "expected_cents": expected_cents,
        "actual_cents": actual_cents,
        "slippage_cents": slippage,
        "expected_spread_cents": expected_spread_cents,
        "realized_spread_cents": realized_spread_cents,
        "execution_delay_ms": execution_delay_ms,
    }


def summary(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n,
               AVG(slippage_cents) AS avg_slip,
               MAX(slippage_cents) AS max_slip,
               MIN(slippage_cents) AS min_slip,
               AVG(realized_spread_cents - expected_spread_cents) AS avg_spread_delta,
               AVG(execution_delay_ms) AS avg_delay_ms
          FROM drift_reports
        """,
    ).fetchone()
    if row is None or row["n"] == 0:
        return {"n": 0, "avg_slippage_cents": 0.0, "avg_spread_delta": 0.0,
                "max_slippage_cents": 0.0, "min_slippage_cents": 0.0,
                "avg_delay_ms": 0.0}
    return {
        "n": int(row["n"]),
        "avg_slippage_cents": float(row["avg_slip"] or 0),
        "max_slippage_cents": float(row["max_slip"] or 0),
        "min_slippage_cents": float(row["min_slip"] or 0),
        "avg_spread_delta": float(row["avg_spread_delta"] or 0),
        "avg_delay_ms": float(row["avg_delay_ms"] or 0),
    }
