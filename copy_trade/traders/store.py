"""Persist master-trader snapshots and retrieve historical series.

Survivorship tracking: traders that disappear from the leaderboard are marked
`active=0` but their snapshot history is kept forever. This lets us measure
whether our scoring system actually predicts future performance.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..db import utc_now_iso
from ..platforms.base import MasterStats


def upsert_master(conn: sqlite3.Connection, m: MasterStats) -> None:
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO masters (uid, platform, nickname, first_seen_at, last_seen_at, active)
        VALUES (?, ?, ?, ?, ?, 1)
        ON CONFLICT(uid) DO UPDATE SET
            nickname = excluded.nickname,
            last_seen_at = excluded.last_seen_at,
            active = 1
        """,
        (m.uid, m.platform, m.nickname, now, now),
    )


def insert_snapshot(conn: sqlite3.Connection, m: MasterStats) -> int:
    cur = conn.execute(
        """
        INSERT INTO trader_snapshots (
            master_uid, captured_at, roi_7d, roi_30d, roi_all,
            mdd, win_rate, total_trades, followers, aum_usdt,
            avg_holding_h, sharpe, raw_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            m.uid, utc_now_iso(),
            m.roi_7d, m.roi_30d, m.roi_all,
            m.mdd, m.win_rate, m.total_trades, m.followers, m.aum_usdt,
            m.avg_holding_h, m.sharpe, json.dumps(m.raw),
        ),
    )
    return int(cur.lastrowid)


def mark_inactive(conn: sqlite3.Connection, platform: str,
                  active_uids: set[str]) -> int:
    """Mark any previously active master not in `active_uids` as inactive."""
    rows = conn.execute(
        "SELECT uid FROM masters WHERE platform=? AND active=1", (platform,)
    ).fetchall()
    deactivated = 0
    for row in rows:
        if row["uid"] not in active_uids:
            conn.execute(
                "UPDATE masters SET active=0, last_seen_at=? WHERE uid=?",
                (utc_now_iso(), row["uid"]),
            )
            deactivated += 1
    return deactivated


def roi_series(conn: sqlite3.Connection, master_uid: str,
               limit: int = 30) -> list[float]:
    """Return last N roi_30d snapshots (oldest first) for rolling Sharpe calc."""
    rows = conn.execute(
        "SELECT roi_30d FROM trader_snapshots WHERE master_uid=? "
        "AND roi_30d IS NOT NULL ORDER BY captured_at DESC LIMIT ?",
        (master_uid, limit),
    ).fetchall()
    return [float(r["roi_30d"]) for r in reversed(rows)]


def latest_snapshot(conn: sqlite3.Connection, master_uid: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM trader_snapshots WHERE master_uid=? "
        "ORDER BY captured_at DESC LIMIT 1",
        (master_uid,),
    ).fetchone()
    return dict(row) if row else None


def track_record_days(conn: sqlite3.Connection, master_uid: str) -> int:
    """Days between first and last snapshot for this master."""
    row = conn.execute(
        "SELECT min(captured_at) AS first, max(captured_at) AS last "
        "FROM trader_snapshots WHERE master_uid=?",
        (master_uid,),
    ).fetchone()
    if not row or not row["first"]:
        return 0
    from datetime import datetime, timezone
    fmt = "%Y-%m-%dT%H:%M:%S%z"
    try:
        t0 = datetime.fromisoformat(row["first"])
        t1 = datetime.fromisoformat(row["last"])
        return max(0, (t1 - t0).days)
    except Exception:  # noqa: BLE001
        return 0


def active_masters(conn: sqlite3.Connection,
                   platform: str | None = None) -> list[dict[str, Any]]:
    if platform:
        rows = conn.execute(
            "SELECT * FROM masters WHERE active=1 AND platform=?", (platform,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM masters WHERE active=1").fetchall()
    return [dict(r) for r in rows]
