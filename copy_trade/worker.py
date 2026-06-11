"""Continuous copy-trade worker.

Runs three APScheduler loops:
  1. Leaderboard poll (default 60 min): refresh master stats, re-score, update DB
  2. Rebalance (default 240 min): compute new allocations, execute subscribe/unsub
  3. PnL poll (default 15 min): pull subscription PnL, update portfolio snapshot

The worker never makes live API calls unless `dry_run=False` is explicitly set.

Run: `python -m copy_trade.worker` or use the CLI `copy-trade worker`.
"""

from __future__ import annotations

import signal
import sys

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .allocation.portfolio import compute_allocations, persist_decisions
from .config import CopyTradeSettings, get_settings
from .db import connect, init_db, transaction, utc_now_iso
from .execution.subscriptions import (
    active_subscriptions, execute_decisions, snapshot_pnl,
)
from .platforms.bitget import BitgetConnector
from .ranking.score import persist_scores, score_traders
from .traders.store import (
    active_masters, insert_snapshot, mark_inactive, upsert_master,
)
from .validation.tracker import update_portfolio_snapshot

log = structlog.get_logger("copy_trade.worker")


def run_leaderboard_poll(dry_run: bool = True,
                         settings: CopyTradeSettings | None = None) -> None:
    settings = settings or get_settings()
    connector = BitgetConnector(settings)
    conn = connect(settings.db_path)
    try:
        masters = connector.all_leaderboard(max_pages=10)
        active_uids: set[str] = set()
        snap_rows: list[dict] = []
        with transaction(conn):
            for m in masters:
                upsert_master(conn, m)
                snap_id = insert_snapshot(conn, m)
                active_uids.add(m.uid)
                snap_rows.append({
                    "master_uid": m.uid, "id": snap_id,
                    "roi_7d": m.roi_7d, "roi_30d": m.roi_30d,
                    "roi_all": m.roi_all, "mdd": m.mdd,
                    "win_rate": m.win_rate, "total_trades": m.total_trades,
                    "followers": m.followers, "aum_usdt": m.aum_usdt,
                    "avg_holding_h": m.avg_holding_h, "sharpe": m.sharpe,
                })
            deactivated = mark_inactive(conn, "bitget", active_uids)
            scores = score_traders(conn, snap_rows, settings)
            persist_scores(conn, scores)
        log.info("leaderboard_poll_done",
                 masters=len(masters), deactivated=deactivated,
                 eligible=sum(1 for s in scores if s.eligible))
    finally:
        conn.close()


def run_rebalance(dry_run: bool = True,
                  settings: CopyTradeSettings | None = None) -> None:
    settings = settings or get_settings()
    connector = BitgetConnector(settings)
    conn = connect(settings.db_path)
    try:
        # Latest scores for eligible masters
        rows = conn.execute(
            """
            SELECT ts.*, ts.composite_score, ts.eligible
            FROM trader_scores ts
            WHERE ts.scored_at = (
                SELECT MAX(scored_at) FROM trader_scores WHERE master_uid = ts.master_uid
            ) AND ts.eligible = 1
            ORDER BY ts.composite_score DESC
            """
        ).fetchall()

        from .ranking.score import TraderScore
        scores = [
            TraderScore(
                master_uid=r["master_uid"],
                snapshot_id=r["snapshot_id"],
                sharpe_est=r["sharpe_est"],
                calmar_est=r["calmar_est"],
                composite=r["composite_score"],
                rank=i + 1,
                eligible=bool(r["eligible"]),
                filter_reason=r["filter_reason"],
            )
            for i, r in enumerate(rows)
        ]
        current = active_subscriptions(conn)
        decisions = compute_allocations(scores, current, settings)
        with transaction(conn):
            persist_decisions(conn, decisions)
            counts = execute_decisions(
                conn, decisions, connector=connector,
                settings=settings, dry_run=dry_run,
            )
        log.info("rebalance_done", counts=counts, dry_run=dry_run)
    finally:
        conn.close()


def run_pnl_poll(dry_run: bool = True,
                 settings: CopyTradeSettings | None = None) -> None:
    settings = settings or get_settings()
    connector = BitgetConnector(settings)
    conn = connect(settings.db_path)
    try:
        if not dry_run:
            snapshot_pnl(conn, connector=connector, settings=settings)
        update_portfolio_snapshot(conn, total_capital=settings.total_capital_usdt)
    finally:
        conn.close()


def start_worker(dry_run: bool = True,
                 settings: CopyTradeSettings | None = None) -> None:
    settings = settings or get_settings()
    init_db(settings.db_path)

    scheduler = BlockingScheduler(timezone="UTC")

    scheduler.add_job(
        lambda: run_leaderboard_poll(dry_run=dry_run, settings=settings),
        trigger=IntervalTrigger(minutes=settings.leaderboard_poll_minutes),
        id="leaderboard_poll",
        max_instances=1,
        replace_existing=True,
        next_run_time=_now(),  # run immediately on startup
    )
    scheduler.add_job(
        lambda: run_rebalance(dry_run=dry_run, settings=settings),
        trigger=IntervalTrigger(minutes=settings.rebalance_minutes),
        id="rebalance",
        max_instances=1,
        replace_existing=True,
        next_run_time=_now(),
    )
    scheduler.add_job(
        lambda: run_pnl_poll(dry_run=dry_run, settings=settings),
        trigger=IntervalTrigger(minutes=settings.pnl_poll_minutes),
        id="pnl_poll",
        max_instances=1,
        replace_existing=True,
        next_run_time=_now(),
    )

    def _shutdown(signo, _frame):
        log.info("worker_shutdown", signal=signo)
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log.info("worker_started",
             leaderboard_poll_min=settings.leaderboard_poll_minutes,
             rebalance_min=settings.rebalance_minutes,
             pnl_poll_min=settings.pnl_poll_minutes,
             dry_run=dry_run)
    scheduler.start()


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


if __name__ == "__main__":
    start_worker(dry_run=True)
