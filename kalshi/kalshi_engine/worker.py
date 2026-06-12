"""Continuous Kalshi worker.

Scans every 5min, health every 15min, daily report at 00:00 UTC,
drift summary hourly. Reconciliation poll every 30s while any order is
in submitted/partial state. Graceful SIGTERM shutdown.
"""

from __future__ import annotations

import signal
import sys
from datetime import datetime, timezone

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .config import get_settings
from .db import connect, init_db
from .ops.health import run_health_checks
from .reports.daily import build_daily_report, persist_daily_report
from .risk import governance as gov
from .scanner import scan
from .validation.drift import summary as drift_summary

log = structlog.get_logger("kalshi.worker")

_shutdown = False


def _scan_job(settings):
    if _shutdown:
        return
    try:
        scan(settings=settings, fetch_orderbook=False)
    except Exception as e:  # noqa: BLE001
        log.error("scan_failed", err=str(e))


def _health_job(settings):
    if _shutdown:
        return
    with connect(settings.db_path) as conn:
        rep = run_health_checks(conn)
        log.info("health", overall=rep.overall,
                 checks={c.component: c.status for c in rep.checks})


def _daily_job(settings):
    with connect(settings.db_path) as conn:
        rep = build_daily_report(conn)
        persist_daily_report(conn, rep)
        log.info("daily_report_done", signals=rep.get("signals_total", 0))


def _drift_job(settings):
    with connect(settings.db_path) as conn:
        s = drift_summary(conn)
        log.info("drift_summary", **{k: str(v) for k, v in s.items() if k != "n"})


def _reconcile_job(settings):
    """Poll any non-terminal live order for fills.

    Stub-safe: if Kalshi GET orders endpoint isn't reachable, log and continue.
    """
    if _shutdown:
        return
    with connect(settings.db_path) as conn:
        rows = conn.execute(
            "SELECT client_order_id, external_order_id FROM live_orders "
            "WHERE status IN ('submitted','partial') AND dry_run = 0"
        ).fetchall()
        for r in rows:
            log.info("reconcile_check", cid=r["client_order_id"],
                     external=r["external_order_id"])
            # Real implementation would GET /portfolio/orders/{id} and call
            # LiveExecutionAdapter.reconcile_order. Stub keeps worker safe to
            # start before reconcile endpoint is exercised.


def start_worker(settings=None) -> None:
    global _shutdown
    settings = settings or get_settings()
    init_db()
    sched = BlockingScheduler(timezone="UTC")
    sched.add_job(lambda: _scan_job(settings),
                  IntervalTrigger(minutes=5), id="scan", max_instances=1,
                  replace_existing=True, next_run_time=_now())
    sched.add_job(lambda: _health_job(settings),
                  IntervalTrigger(minutes=15), id="health", max_instances=1,
                  replace_existing=True, next_run_time=_now())
    sched.add_job(lambda: _drift_job(settings),
                  IntervalTrigger(hours=1), id="drift", max_instances=1,
                  replace_existing=True)
    sched.add_job(lambda: _daily_job(settings),
                  CronTrigger(hour=0, minute=0), id="daily", max_instances=1,
                  replace_existing=True)
    sched.add_job(lambda: _reconcile_job(settings),
                  IntervalTrigger(seconds=30), id="reconcile", max_instances=1,
                  replace_existing=True)

    def _shutdown_handler(signo, _frame):
        global _shutdown
        _shutdown = True
        log.info("worker_shutdown", signal=signo)
        sched.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)
    log.info("worker_started")
    sched.start()


def _now():
    return datetime.now(timezone.utc)


if __name__ == "__main__":
    start_worker()
