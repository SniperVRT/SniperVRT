"""Look-ahead bias audit: ensure no future data leaked into past decisions."""

from __future__ import annotations

import sqlite3


def audit(conn: sqlite3.Connection) -> dict:
    """Return any (score row, snapshot) pair where snapshot.captured_at > score.scored_at."""
    rows = conn.execute(
        """
        SELECT ts.id AS score_id, ts.scored_at, t.captured_at, t.id AS snap_id
        FROM trader_scores ts
        JOIN trader_snapshots t ON t.id = ts.snapshot_id
        WHERE t.captured_at > ts.scored_at
        """
    ).fetchall()
    violations = [dict(r) for r in rows]
    return {"n_violations": len(violations), "violations": violations[:50]}
