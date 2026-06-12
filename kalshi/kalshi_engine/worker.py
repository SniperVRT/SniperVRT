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
    """Poll Kalshi for fills on any non-terminal live order and apply them.

    Network failures log and continue — the next 30s tick retries.
    """
    if _shutdown:
        return
    with connect(settings.db_path) as conn:
        rows = conn.execute(
            "SELECT client_order_id, external_order_id FROM live_orders "
            "WHERE status IN ('submitted','partial') AND dry_run = 0"
        ).fetchall()
        if not rows:
            return
        from .connectors.kalshi import KalshiClient
        from .execution.live import LiveExecutionAdapter
        client = KalshiClient(settings)
        adapter = LiveExecutionAdapter(conn=conn, settings=settings, client=client)
        for r in rows:
            ext = r["external_order_id"]
            if not ext:
                continue
            try:
                data = client._get(f"/portfolio/orders/{ext}", auth=True)
                order = data.get("order") or {}
                filled = int(order.get("filled_count") or 0)
                if filled > 0:
                    avg_cents = order.get("average_fill_price")
                    adapter.reconcile_order(r["client_order_id"], external_fills=[
                        {"qty": filled,
                         "price_cents": int(avg_cents or 0),
                         "external_fill_id": str(ext)},
                    ])
                    log.info("reconcile_applied", cid=r["client_order_id"],
                             filled=filled)
            except Exception as e:  # noqa: BLE001
                log.warning("reconcile_poll_failed", cid=r["client_order_id"],
                            err=str(e))


def _start_health_server(settings, port: int = 8080):
    """Minimal stdlib /health endpoint mirroring copy_trade's healthserver."""
    import json as _json
    import threading
    import time as _time
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            return
        def do_GET(self):
            if self.path != "/health":
                self.send_response(404); self.end_headers(); return
            try:
                with connect(settings.db_path) as conn:
                    locks = conn.execute(
                        "SELECT COUNT(*) FROM governance_locks WHERE released_at IS NULL"
                    ).fetchone()[0]
                ok = locks == 0
                body = _json.dumps({"status": "ok" if ok else "degraded",
                                     "active_locks": locks,
                                     "timestamp": _time.time()}).encode()
                self.send_response(200 if ok else 503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:  # noqa: BLE001
                self.send_response(500); self.end_headers()
                self.wfile.write(str(e).encode())

    server = HTTPServer(("0.0.0.0", port), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def start_worker(settings=None) -> None:
    global _shutdown
    settings = settings or get_settings()
    init_db()
    health_server = _start_health_server(settings)
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
        health_server.shutdown()
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
