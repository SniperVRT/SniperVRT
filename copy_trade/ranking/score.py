"""Trader scoring engine.

Council quant analysis + research findings:

Scoring formula (drawdown-adjusted momentum, from arXiv meta-CTA research):
  primary = rolling_roi_30d / (1 + abs(current_mdd))
  This naturally penalises traders currently in drawdown even with strong history.

Composite score = weighted blend of:
  1. Sharpe estimate (0.40): risk-adjusted return
  2. Calmar ratio    (0.25): annualised return / max drawdown
  3. Profit factor   (0.15): win/loss ratio proxy
  4. Win rate        (0.10): secondary signal
  5. Consistency     (0.10): % of periods profitable

Sharpe estimation:
  - Bitget does not expose Sharpe in its public API.
  - Estimate from roi_30d snapshot series (annualise as monthly:
    sharpe = mean(returns)/std(returns) * sqrt(12)).
  - Minimum 3 snapshots for any Sharpe estimate; else None.

Calmar = annualized_roi_all / abs(mdd). Capped at 10 if MDD near zero.
Minimum track record: 30 days (configurable). Hard reject at 90 days is
recommended by research for more predictive scoring, but 30d works for v0
given the June 22 deadline — increase to 90 after first 90 days of data.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from typing import Any

import structlog

from ..config import CopyTradeSettings, get_settings
from ..db import utc_now_iso
from ..traders.store import roi_series, track_record_days

log = structlog.get_logger("copy_trade.scoring")


@dataclass
class TraderScore:
    master_uid: str
    snapshot_id: int
    sharpe_est: float | None
    calmar_est: float | None
    composite: float
    rank: int
    eligible: bool
    filter_reason: str | None


def _sharpe_from_series(returns: list[float]) -> float | None:
    if len(returns) < 3:
        return None
    n = len(returns)
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(variance) if variance > 0 else 0.0
    if std < 1e-9:
        return None
    return (mean / std) * math.sqrt(12)  # annualise monthly returns


def _calmar(roi_all: float | None, mdd: float | None) -> float | None:
    if roi_all is None or mdd is None:
        return None
    abs_mdd = abs(mdd)
    if abs_mdd < 0.001:
        return min(10.0, roi_all * 12) if roi_all > 0 else 0.0
    annualised = roi_all  # platform's roi_all already annualised for most; use as-is
    return max(0.0, annualised / abs_mdd)


def _normalize(values: list[float]) -> list[float]:
    """Min-max normalize a list of floats to [0, 1]."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def score_traders(
    conn: sqlite3.Connection,
    snapshots: list[dict[str, Any]],
    settings: CopyTradeSettings | None = None,
) -> list[TraderScore]:
    """Compute composite scores for a batch of trader snapshots.

    `snapshots` is a list of dicts from `trader_snapshots` rows plus the
    snapshot_id. Returned list is sorted by composite score descending.
    """
    settings = settings or get_settings()
    scored: list[dict[str, Any]] = []

    for snap in snapshots:
        uid = snap["master_uid"]
        snap_id = snap["id"]

        # Eligibility filters
        days = track_record_days(conn, uid)
        eligible = True
        reason: str | None = None

        if days < settings.min_track_record_days:
            eligible = False
            reason = f"track_record_{days}d<{settings.min_track_record_days}d"

        mdd = snap.get("mdd")
        if mdd is not None and abs(mdd) > settings.max_mdd_pct:
            eligible = False
            reason = f"mdd_{abs(mdd):.0%}>{settings.max_mdd_pct:.0%}"

        followers = snap.get("followers") or 0
        if followers < settings.min_followers:
            eligible = False
            reason = f"followers_{followers}<{settings.min_followers}"

        win_rate = snap.get("win_rate") or 0.0
        if win_rate < settings.min_win_rate:
            eligible = False
            reason = f"win_rate_{win_rate:.0%}<{settings.min_win_rate:.0%}"

        # Compute Sharpe
        platform_sharpe = snap.get("sharpe")
        series = roi_series(conn, uid, limit=24)
        computed_sharpe = _sharpe_from_series(series)
        sharpe = platform_sharpe if platform_sharpe is not None else computed_sharpe

        if sharpe is not None and sharpe < settings.min_sharpe:
            eligible = False
            reason = reason or f"sharpe_{sharpe:.2f}<{settings.min_sharpe}"

        calmar = _calmar(snap.get("roi_all"), mdd)
        profit_factor = _estimate_profit_factor(win_rate, snap.get("roi_all") or 0.0)
        consistency = _estimate_consistency(series)

        scored.append({
            "uid": uid,
            "snap_id": snap_id,
            "sharpe": sharpe,
            "calmar": calmar,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "consistency": consistency,
            "eligible": eligible,
            "reason": reason,
        })

    # Normalize each metric across all candidates (including ineligible, so
    # scores are comparable), then weight.
    def _col(key: str) -> list[float]:
        return [s[key] if s[key] is not None else 0.0 for s in scored]

    norm_sharpe = _normalize(_col("sharpe"))
    norm_calmar = _normalize(_col("calmar"))
    norm_wr = _normalize(_col("win_rate"))
    norm_pf = _normalize(_col("profit_factor"))
    norm_cons = _normalize(_col("consistency"))

    w = settings
    results: list[TraderScore] = []
    for i, s in enumerate(scored):
        composite = (
            w.weight_sharpe * norm_sharpe[i]
            + w.weight_calmar * norm_calmar[i]
            + w.weight_win_rate * norm_wr[i]
            + w.weight_profit_factor * norm_pf[i]
            + w.weight_consistency * norm_cons[i]
        )
        results.append(TraderScore(
            master_uid=s["uid"],
            snapshot_id=s["snap_id"],
            sharpe_est=s["sharpe"],
            calmar_est=s["calmar"],
            composite=composite,
            rank=0,  # filled below
            eligible=s["eligible"],
            filter_reason=s["reason"],
        ))

    # Sort eligible first, then by composite desc.
    results.sort(key=lambda x: (not x.eligible, -x.composite))
    for i, r in enumerate(results):
        r.rank = i + 1

    return results


def persist_scores(conn: sqlite3.Connection, scores: list[TraderScore]) -> None:
    now = utc_now_iso()
    for s in scores:
        conn.execute(
            """
            INSERT INTO trader_scores (
                master_uid, snapshot_id, scored_at,
                sharpe_est, calmar_est, composite_score,
                rank_at_time, eligible, filter_reason
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                s.master_uid, s.snapshot_id, now,
                s.sharpe_est, s.calmar_est, s.composite,
                s.rank, int(s.eligible), s.filter_reason,
            ),
        )


def _estimate_profit_factor(win_rate: float, roi_all: float) -> float:
    """Estimate profit factor from win rate and total ROI.

    This is approximate. If the platform provides gross wins/losses directly,
    prefer those.
    """
    if win_rate <= 0:
        return 0.0
    if win_rate >= 1.0:
        return 10.0
    loss_rate = 1.0 - win_rate
    if loss_rate < 1e-9:
        return 10.0
    # Assume average win / average loss is 1.0 as baseline, adjust by ROI.
    avg_win_loss_ratio = max(0.1, 1.0 + roi_all)
    pf = (win_rate * avg_win_loss_ratio) / loss_rate
    return min(10.0, max(0.0, pf))


def _estimate_consistency(roi_series_values: list[float]) -> float:
    """% of periods with positive return (proxy for consistency)."""
    if not roi_series_values:
        return 0.5
    positive = sum(1 for r in roi_series_values if r > 0)
    return positive / len(roi_series_values)
