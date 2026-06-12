"""Capital allocation engine.

Given a ranked list of trader scores, decide how much capital to allocate
to each and whether to subscribe, rebalance, or unsubscribe.

Algorithm (score-weighted with constraints):
  1. Filter to eligible traders only, take top N by composite score.
  2. Compute raw weights proportional to composite score.
  3. Clip to [min_pct, max_pct] and renormalise.
  4. Convert fractions to USDT amounts (floor to min_allocation_usdt).
  5. Compare against current subscriptions → emit action per master.

Rebalance threshold: only emit a 'rebalance' action if the target allocation
differs from current by > 10% (relative). This avoids constant churn.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

import structlog

from ..config import CopyTradeSettings, get_settings
from ..db import utc_now_iso
from ..ranking.score import TraderScore

log = structlog.get_logger("copy_trade.allocation")

REBALANCE_THRESHOLD = 0.10  # 10% relative drift triggers rebalance


@dataclass
class AllocationDecision:
    master_uid: str
    action: str               # subscribe | keep | rebalance | unsubscribe
    current_usdt: float
    target_usdt: float
    score: float
    reason: str


def compute_allocations(
    scores: list[TraderScore],
    current_subs: list[dict[str, Any]],
    settings: CopyTradeSettings | None = None,
) -> list[AllocationDecision]:
    """Return allocation decisions for all masters (subscribe/keep/rebalance/unsub)."""
    settings = settings or get_settings()

    eligible = [s for s in scores if s.eligible][:settings.max_masters]
    eligible_uids = {s.master_uid for s in eligible}

    current_map: dict[str, float] = {
        r["master_uid"]: float(r["allocated_usdt"]) for r in current_subs
    }
    subscribed_uids = set(current_map)

    # Target allocations for eligible traders
    target = _score_weighted_allocation(eligible, settings)

    decisions: list[AllocationDecision] = []

    # Traders to subscribe or rebalance
    for score in eligible:
        uid = score.master_uid
        t_usdt = target.get(uid, 0.0)
        c_usdt = current_map.get(uid, 0.0)

        if uid not in subscribed_uids:
            if t_usdt >= settings.min_allocation_usdt:
                decisions.append(AllocationDecision(
                    master_uid=uid, action="subscribe",
                    current_usdt=0.0, target_usdt=t_usdt,
                    score=score.composite,
                    reason=f"new_top_performer score={score.composite:.3f}",
                ))
        else:
            drift = abs(t_usdt - c_usdt) / max(c_usdt, 1.0)
            if drift > REBALANCE_THRESHOLD:
                decisions.append(AllocationDecision(
                    master_uid=uid, action="rebalance",
                    current_usdt=c_usdt, target_usdt=t_usdt,
                    score=score.composite,
                    reason=f"drift={drift:.0%}>threshold",
                ))
            else:
                decisions.append(AllocationDecision(
                    master_uid=uid, action="keep",
                    current_usdt=c_usdt, target_usdt=t_usdt,
                    score=score.composite,
                    reason="within_threshold",
                ))

    # Unsubscribe traders no longer in eligible set
    for uid in subscribed_uids - eligible_uids:
        c_usdt = current_map[uid]
        decisions.append(AllocationDecision(
            master_uid=uid, action="unsubscribe",
            current_usdt=c_usdt, target_usdt=0.0,
            score=0.0,
            reason="not_in_eligible_set",
        ))

    return decisions


def persist_decisions(conn: sqlite3.Connection,
                      decisions: list[AllocationDecision]) -> None:
    now = utc_now_iso()
    for d in decisions:
        if d.action in ("subscribe", "rebalance", "keep", "unsubscribe"):
            conn.execute(
                """
                INSERT INTO allocations (decided_at, master_uid, allocated_usdt,
                    allocation_pct, score, action)
                VALUES (?,?,?,?,?,?)
                """,
                (now, d.master_uid, d.target_usdt, 0.0, d.score, d.action),
            )


def _score_weighted_allocation(
    eligible: list[TraderScore],
    settings: CopyTradeSettings,
) -> dict[str, float]:
    """Score-weighted capital allocation with floor/cap constraints."""
    if not eligible:
        return {}

    total = settings.total_capital_usdt
    raw_weights = {s.master_uid: max(0.0, s.composite) for s in eligible}
    total_weight = sum(raw_weights.values())
    if total_weight < 1e-9:
        # All scores are zero — equal weight
        n = len(eligible)
        raw_weights = {s.master_uid: 1.0 / n for s in eligible}
        total_weight = 1.0

    # Normalise then clip to [min_pct, max_pct]
    fracs = {uid: w / total_weight for uid, w in raw_weights.items()}
    clipped = {uid: min(settings.max_allocation_pct,
                        max(settings.min_allocation_pct, f))
               for uid, f in fracs.items()}

    # Renormalise after clipping
    total_clipped = sum(clipped.values())
    if total_clipped > 1.0:
        clipped = {uid: f / total_clipped for uid, f in clipped.items()}

    return {uid: round(f * total, 2) for uid, f in clipped.items()}
