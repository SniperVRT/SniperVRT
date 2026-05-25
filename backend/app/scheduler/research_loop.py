"""Single background thread that periodically runs each research pipeline on its
own cadence. Crash-safe; each task is independently try/except'd.

Default cadences (seconds):
  news_ingest:        600    (10 min)
  news_attribution:  1800    (30 min)
  sentiment:          600    (10 min)
  micro_signals:      300    ( 5 min)
  regime_v2:          120    ( 2 min)
  agents:             900    (15 min)
  edge_discovery:    3600    ( 1 h)
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from backend.app.intelligence import (
    attribute_market_reactions, collect_micro_signals, detect_regime_v2,
    ingest_news, snapshot_sentiment,
)
from backend.app.memory import remember
from backend.app.research import discover_edges, run_all_agents

log = logging.getLogger(__name__)


@dataclass
class Task:
    name: str
    fn: Callable[[], dict]
    interval_s: int
    last_run: int = 0
    last_result: dict = field(default_factory=dict)
    last_error: str = ""
    runs: int = 0
    errors: int = 0


class ResearchScheduler:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._running = False
        self._lock = threading.Lock()
        self._register_defaults()

    def _register_defaults(self) -> None:
        self.register("news_ingest", lambda: ingest_news(), 600)
        self.register("news_attribution", lambda: attribute_market_reactions(), 1800)
        self.register("sentiment", lambda: snapshot_sentiment(), 600)
        self.register("micro_signals", lambda: collect_micro_signals(), 300)
        self.register("regime_v2", lambda: detect_regime_v2(), 120)
        self.register("agents", lambda: run_all_agents(), 900)
        self.register("edge_discovery", lambda: discover_edges(), 3600)

    def register(self, name: str, fn: Callable[[], dict], interval_s: int) -> None:
        self._tasks[name] = Task(name=name, fn=fn, interval_s=interval_s)

    def status(self) -> dict:
        return {
            "running": self._running,
            "tasks": {
                t.name: {
                    "interval_s": t.interval_s,
                    "last_run": t.last_run,
                    "next_run_in": max(0, (t.last_run + t.interval_s) - int(time.time())) if t.last_run else 0,
                    "runs": t.runs,
                    "errors": t.errors,
                    "last_error": t.last_error,
                    "last_result_summary": _summarize(t.last_result),
                } for t in self._tasks.values()
            },
        }

    def force_run(self, name: str) -> dict:
        t = self._tasks.get(name)
        if t is None:
            raise KeyError(f"unknown task: {name}")
        return self._run_task(t)

    def _run_task(self, t: Task) -> dict:
        try:
            res = t.fn() or {}
            t.last_result = res
            t.last_error = ""
            t.runs += 1
            t.last_run = int(time.time())
            return {"ok": True, "task": t.name, "result": _summarize(res)}
        except Exception as e:
            t.errors += 1
            t.last_error = str(e)[:200]
            t.last_run = int(time.time())
            log.exception("research task '%s' failed", t.name)
            try:
                remember("anomaly", f"scheduler:{t.name}",
                         f"task error: {e}", weight=0.4,
                         evidence={"task": t.name})
            except Exception:
                pass
            return {"ok": False, "task": t.name, "error": t.last_error}

    def _loop(self) -> None:
        self._running = True
        log.info("research scheduler started with %d tasks", len(self._tasks))
        try:
            while not self._stop.is_set():
                now = int(time.time())
                for t in self._tasks.values():
                    if self._stop.is_set():
                        break
                    if t.last_run == 0 or now - t.last_run >= t.interval_s:
                        self._run_task(t)
                # tick every 10s for new work
                self._stop.wait(10)
        finally:
            self._running = False
            log.info("research scheduler stopped")

    def start(self) -> dict:
        with self._lock:
            if self._running:
                return {"running": True, "already": True}
            self._stop = threading.Event()
            self._thread = threading.Thread(target=self._loop,
                                            name="research-scheduler", daemon=True)
            self._thread.start()
            return {"running": True, "tasks": list(self._tasks.keys())}

    def stop(self) -> dict:
        with self._lock:
            if not self._running:
                return {"running": False}
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=5)
            return {"stopped": True}


def _summarize(result: dict) -> dict:
    """Keep DB / logs small: drop huge nested lists from task results."""
    if not isinstance(result, dict):
        return {}
    out = {}
    for k, v in result.items():
        if isinstance(v, list) and len(v) > 5:
            out[k] = f"<list n={len(v)}>"
        elif isinstance(v, dict) and len(v) > 12:
            out[k] = f"<dict n={len(v)}>"
        else:
            out[k] = v
    return out


_scheduler: Optional[ResearchScheduler] = None


def get_scheduler() -> ResearchScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = ResearchScheduler()
    return _scheduler
