"""Portfolio-level risk: correlation matrix, same-leader concentration, locked exposure."""

from __future__ import annotations

import json
import math
import sqlite3
from typing import Any

import structlog

from ..config import CopyTradeSettings, get_settings
from ..db import utc_now_iso
from ..execution.subscriptions import active_subscriptions
from ..ranking.correlation import (
    pearson_correlation, returns_from_equity_curve,
)

log = structlog.get_logger("copy_trade.portfolio_risk")

LOCK_CONCENTRATION = "concentration"


def _vault_returns(conn: sqlite3.Connection, uid: str) -> list[float]:
    row = conn.execute(
        "SELECT raw_json FROM trader_snapshots WHERE master_uid=? "
        "ORDER BY captured_at DESC LIMIT 1", (uid,),
    ).fetchone()
    if not row or not row["raw_json"]:
        return []
    try:
        raw = json.loads(row["raw_json"])
        portfolio = raw.get("portfolio") or []
        for entry in portfolio:
            if isinstance(entry, list) and len(entry) == 2 and entry[0] == "month":
                hist = (entry[1] or {}).get("accountValueHistory") or []
                return returns_from_equity_curve(hist)
    except Exception:  # noqa: BLE001
        return []
    return []


def _leader_for(conn: sqlite3.Connection, uid: str) -> str | None:
    row = conn.execute(
        "SELECT raw_json FROM trader_snapshots WHERE master_uid=? "
        "ORDER BY captured_at DESC LIMIT 1", (uid,),
    ).fetchone()
    if row and row["raw_json"]:
        try:
            return json.loads(row["raw_json"]).get("leader")
        except Exception:  # noqa: BLE001
            return None
    return None


def compute_correlation_matrix(conn: sqlite3.Connection) -> dict[str, Any]:
    subs = active_subscriptions(conn)
    rets = {s["master_uid"]: _vault_returns(conn, s["master_uid"]) for s in subs}
    uids = [u for u, r in rets.items() if len(r) >= 5]
    pairs: list[tuple[str, str, float]] = []
    for i, a in enumerate(uids):
        for b in uids[i + 1:]:
            c = pearson_correlation(rets[a], rets[b])
            if c is not None:
                pairs.append((a, b, c))
    avg = sum(c for _, _, c in pairs) / len(pairs) if pairs else 0.0
    mx = max((c for _, _, c in pairs), default=0.0)
    return {"pairs": pairs, "avg": avg, "max": mx, "n_pairs": len(pairs)}


def same_leader_concentration(conn: sqlite3.Connection) -> dict[str, float]:
    out: dict[str, float] = {}
    for s in active_subscriptions(conn):
        leader = _leader_for(conn, s["master_uid"]) or s["master_uid"]
        out[leader] = out.get(leader, 0.0) + float(s["allocated_usdt"])
    return out


def liquidity_summary(conn: sqlite3.Connection) -> dict[str, float]:
    import time as _t
    now_ms = int(_t.time() * 1000)
    locked, unlocked = 0.0, 0.0
    for s in active_subscriptions(conn):
        lk = s.get("lockup_until_ms") or 0
        if lk and now_ms < int(lk):
            locked += float(s["allocated_usdt"])
        else:
            unlocked += float(s["allocated_usdt"])
    total = locked + unlocked
    return {"locked_usdt": locked, "unlocked_usdt": unlocked,
            "locked_pct": locked / total if total else 0.0}


def snapshot(conn: sqlite3.Connection,
             settings: CopyTradeSettings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    corr = compute_correlation_matrix(conn)
    leaders = same_leader_concentration(conn)
    total_alloc = sum(leaders.values()) or 1.0
    max_leader_pct = max(leaders.values()) / total_alloc if leaders else 0.0
    liq = liquidity_summary(conn)

    conn.execute(
        "INSERT INTO portfolio_risk_snapshots "
        "(captured_at, avg_pairwise_corr, max_pairwise_corr, n_pairs, "
        "same_leader_max_pct, locked_pct, var_95_usdt, cvar_95_usdt) "
        "VALUES (?,?,?,?,?,?,NULL,NULL)",
        (utc_now_iso(), corr["avg"], corr["max"], corr["n_pairs"],
         max_leader_pct, liq["locked_pct"]),
    )

    engage = False
    reason = ""
    if corr["avg"] > 0.5:
        engage, reason = True, f"avg_corr={corr['avg']:.2f}>0.5"
    elif max_leader_pct > 0.30:
        engage, reason = True, f"max_leader_pct={max_leader_pct:.0%}>30%"
    elif liq["locked_pct"] > settings.max_locked_pct:
        engage, reason = True, f"locked_pct={liq['locked_pct']:.0%}>cap"
    if engage:
        conn.execute(
            "INSERT INTO safety_events (event_at, event_type, detail) "
            "VALUES (?, 'concentration', ?)",
            (utc_now_iso(), reason),
        )
    return {"corr": corr, "leaders": leaders, "liquidity": liq,
            "engage_lock": engage, "reason": reason}
