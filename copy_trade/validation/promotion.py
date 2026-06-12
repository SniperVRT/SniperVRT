"""Deterministic promotion gates: block --live unless every condition is green."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..config import CopyTradeSettings, get_settings
from .tracker import score_prediction_accuracy


@dataclass
class PromotionReport:
    ready: bool
    blockers: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


def check_ready_for_live(conn: sqlite3.Connection,
                          settings: CopyTradeSettings | None = None) -> PromotionReport:
    settings = settings or get_settings()
    blockers: list[str] = []
    details: dict = {}

    # 1. >= 7 calendar days of trader_snapshots history
    row = conn.execute(
        "SELECT MIN(captured_at) AS first, COUNT(*) AS n FROM trader_snapshots"
    ).fetchone()
    history_days = 0
    if row and row["first"]:
        try:
            first = datetime.fromisoformat(row["first"])
            history_days = (datetime.now(timezone.utc) - first).days
        except Exception:  # noqa: BLE001
            pass
    details["history_days"] = history_days
    if history_days < 7:
        blockers.append(f"history_days={history_days}<7")

    # 2. >= 3 successful paper-rebalance runs in last 7d
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    paper_runs = conn.execute(
        "SELECT COUNT(*) FROM paper_rebalance_runs WHERE ran_at>=? AND success=1",
        (cutoff,),
    ).fetchone()[0]
    details["paper_runs_7d"] = paper_runs
    if paper_runs < 3:
        blockers.append(f"paper_runs_7d={paper_runs}<3")

    # 3. Spearman rank corr (scoring vs realised PnL) >= 0.20
    pred = score_prediction_accuracy(conn, lookback_days=14)
    corr = pred.get("rank_correlation")
    details["spearman_corr"] = corr
    if corr is None or corr < 0.20:
        blockers.append(f"spearman_corr={corr}<0.20")

    # 4. validation_runs row with passed_at within 30 days
    cutoff30 = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    rt = conn.execute(
        "SELECT MAX(passed_at) AS last FROM validation_runs "
        "WHERE kind='testnet_roundtrip' AND passed_at>=?",
        (cutoff30,),
    ).fetchone()
    details["last_testnet_roundtrip"] = rt["last"] if rt else None
    if not rt or not rt["last"]:
        blockers.append("testnet_roundtrip_missing_or_stale")

    # 5. settings flags
    if not getattr(settings, "live_enabled", False):
        blockers.append("live_enabled=False")
    if getattr(settings, "live_dry_run", True):
        blockers.append("live_dry_run=True")
    if not settings.live_require_manual_approval:
        blockers.append("live_require_manual_approval=False")

    # 6. no unresolved safety_events
    open_events = conn.execute(
        "SELECT COUNT(*) FROM safety_events WHERE resolved_at IS NULL"
    ).fetchone()[0]
    details["open_safety_events"] = open_events
    if open_events:
        blockers.append(f"open_safety_events={open_events}")

    # 7. emergency_force_unwind enabled
    if not settings.emergency_force_unwind:
        blockers.append("emergency_force_unwind=False")

    return PromotionReport(ready=not blockers, blockers=blockers, details=details)
