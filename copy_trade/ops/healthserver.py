"""Stdlib HTTP healthcheck + Prometheus metrics endpoint."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from ..config import CopyTradeSettings, get_settings
from ..db import connect


def build_handler(settings: CopyTradeSettings):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return
        def do_GET(self):
            if self.path == "/health":
                self._health()
            elif self.path == "/metrics":
                self._metrics()
            else:
                self.send_response(404); self.end_headers()
        def _health(self):
            try:
                with connect(settings.db_path) as c:
                    locks = c.execute(
                        "SELECT COUNT(*) FROM safety_events WHERE resolved_at IS NULL"
                    ).fetchone()[0]
                    subs = c.execute(
                        "SELECT COUNT(*) FROM subscriptions WHERE status='active' AND mode='live'"
                    ).fetchone()[0]
                ok = locks == 0
                body = json.dumps({"status": "ok" if ok else "degraded",
                                     "checks": {"db": "ok",
                                                "unresolved_safety_events": locks,
                                                "active_live_subs": subs},
                                     "timestamp": time.time()}).encode()
                self.send_response(200 if ok else 503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:  # noqa: BLE001
                self.send_response(500); self.end_headers()
                self.wfile.write(str(e).encode())
        def _metrics(self):
            try:
                with connect(settings.db_path) as c:
                    row = c.execute(
                        "SELECT * FROM portfolio_snapshots ORDER BY captured_at DESC LIMIT 1"
                    ).fetchone() or {}
                    locks = c.execute(
                        "SELECT COUNT(*) FROM safety_events WHERE resolved_at IS NULL"
                    ).fetchone()[0]
                    subs = c.execute(
                        "SELECT COUNT(*) FROM subscriptions WHERE status='active' AND mode='live'"
                    ).fetchone()[0]
                lines = [
                    f"copytrade_deployed_capital_usdt {float(row.get('deployed_usdt') or 0)}",
                    f"copytrade_active_subscriptions {subs}",
                    f"copytrade_active_locks {locks}",
                    f"copytrade_drawdown_pct {float(row.get('drawdown_pct') or 0)}",
                ]
                body = ("\n".join(lines) + "\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:  # noqa: BLE001
                self.send_response(500); self.end_headers()
                self.wfile.write(str(e).encode())
    return H


def start_in_background(port: int = 8081,
                         settings: CopyTradeSettings | None = None) -> HTTPServer:
    settings = settings or get_settings()
    server = HTTPServer(("0.0.0.0", port), build_handler(settings))
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server
