"""End-to-end dry-run rehearsal.

Walks the complete workflow on synthetic or live-readonly inputs without
ever touching real money:

  signal generation → approval → governance → order preview → submit
  (dry) → reconcile (synthetic fill) → portfolio update → drift check →
  shutdown validation.

Every scenario produces a structured pass/fail with detail. The audit
JSON is stored in `rehearsal_runs` for later review.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

from ..config import Settings, get_settings
from ..connectors.kalshi import Orderbook
from ..core.signals import Signal
from ..db import utc_now_iso
from ..execution.live import LiveExecutionAdapter
from ..risk import governance as gov
from ..validation.drift import compute_drift

log = structlog.get_logger("rehearsal")


@dataclass
class Scenario:
    name: str
    passed: bool
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class RehearsalReport:
    started_at: str
    finished_at: str
    scenarios: list[Scenario]
    passed: int
    failed: int
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "passed": self.passed,
            "failed": self.failed,
            "notes": self.notes,
            "scenarios": [asdict(s) for s in self.scenarios],
        }


def _book(yes: list[tuple[int, int]], no: list[tuple[int, int]]) -> Orderbook:
    return Orderbook(ticker="REH", yes=yes, no=no)


def _signal(**o: Any) -> Signal:
    base = dict(
        ticker="REH", side="yes", strategy="ensemble",
        fair_prob=0.6, implied_prob=0.42, edge=0.18, expected_value=0.4,
        confidence=0.8, max_price_cents=44, suggested_size_usd=2.0,
        thesis="rehearsal",
    )
    base.update(o)
    return Signal(**base)


def _seed_market(conn: sqlite3.Connection, ticker: str = "REH") -> None:
    now = utc_now_iso()
    conn.execute(
        "INSERT OR IGNORE INTO markets (ticker, title, status, first_seen_at, last_seen_at) "
        "VALUES (?, 'Rehearsal market', 'active', ?, ?)",
        (ticker, now, now),
    )


def _scenario_preview_dry_run(conn: sqlite3.Connection, settings: Settings) -> Scenario:
    adapter = LiveExecutionAdapter(conn=conn, settings=settings)
    p = adapter.preview(signal=_signal(), book=_book([(42, 100)], [(58, 100)]))
    ok = (p.dry_run is True) and ("dry_run" in p.reasons or not settings.live_enabled)
    return Scenario(name="preview_dry_run", passed=ok, detail=asdict(p))


def _scenario_submit_blocked_without_approval(conn: sqlite3.Connection, settings: Settings) -> Scenario:
    adapter = LiveExecutionAdapter(conn=conn, settings=settings)
    sig = _signal(ticker="REH_NA")
    _seed_market(conn, sig.ticker)
    p = adapter.preview(signal=sig, book=_book([(42, 100)], [(58, 100)]))
    res = adapter.submit(signal=sig, preview=p)
    ok = res.dry_run and res.status == "cancelled"
    return Scenario(name="submit_blocked_without_approval", passed=ok, detail=asdict(res))


def _scenario_duplicate_order_blocked(conn: sqlite3.Connection, settings: Settings) -> Scenario:
    adapter = LiveExecutionAdapter(conn=conn, settings=settings)
    sig = _signal(ticker="REH_DUP")
    _seed_market(conn, sig.ticker)
    # first order goes through (cancelled dry-run), insert a fake submitted
    # one to simulate an in-flight order, then attempt a second submit.
    p1 = adapter.preview(signal=sig, book=_book([(42, 100)], [(58, 100)]))
    conn.execute(
        "UPDATE live_orders SET status='submitted' WHERE client_order_id=?",
        (p1.client_order_id,),
    )
    p2 = adapter.preview(signal=sig, book=_book([(42, 100)], [(58, 100)]))
    res = adapter.submit(signal=sig, preview=p2)
    ok = res.status == "rejected" and res.error == "duplicate_order"
    # Release the lock the duplicate path engaged so the next scenarios run.
    gov.release_lock(conn, gov.LOCK_DUPLICATE_ORDER)
    return Scenario(name="duplicate_order_blocked", passed=ok, detail=asdict(res))


def _scenario_governance_lock_blocks_submit(conn: sqlite3.Connection, settings: Settings) -> Scenario:
    gov.engage_lock(conn, gov.LOCK_EMERGENCY, "rehearsal")
    adapter = LiveExecutionAdapter(conn=conn, settings=settings)
    sig = _signal(ticker="REH_LOCK")
    _seed_market(conn, sig.ticker)
    p = adapter.preview(signal=sig, book=_book([(42, 100)], [(58, 100)]))
    res = adapter.submit(signal=sig, preview=p)
    ok = res.status == "cancelled" and not p.governance_ok
    gov.release_lock(conn, gov.LOCK_EMERGENCY)
    return Scenario(name="governance_lock_blocks_submit", passed=ok,
                    detail={"preview_reasons": p.reasons, "submit": asdict(res)})


def _scenario_reconcile_records_fills(conn: sqlite3.Connection, settings: Settings) -> Scenario:
    adapter = LiveExecutionAdapter(conn=conn, settings=settings)
    sig = _signal(ticker="REH_FILL")
    _seed_market(conn, sig.ticker)
    p = adapter.preview(signal=sig, book=_book([(42, 100)], [(58, 100)]))
    # Force the order into the "submitted" state so reconcile has something
    # plausible to act on.
    conn.execute(
        "UPDATE live_orders SET status='submitted' WHERE client_order_id=?",
        (p.client_order_id,),
    )
    adapter.reconcile_order(p.client_order_id, external_fills=[
        {"qty": p.qty, "price_cents": 42, "fee_usd": 0.01,
         "external_fill_id": "test-1"},
    ])
    row = conn.execute(
        "SELECT status, filled_qty FROM live_orders WHERE client_order_id=?",
        (p.client_order_id,),
    ).fetchone()
    ok = row["status"] == "filled" and int(row["filled_qty"]) == p.qty
    return Scenario(name="reconcile_records_fills", passed=ok,
                    detail={"status": row["status"], "filled_qty": int(row["filled_qty"])})


def _scenario_drift_computes(conn: sqlite3.Connection, settings: Settings) -> Scenario:
    rep = compute_drift(conn, expected_cents=42.0, actual_cents=43.5,
                       expected_spread_cents=2.0, realized_spread_cents=3.0,
                       ticker="REH_FILL", execution_delay_ms=120.0)
    ok = rep["slippage_cents"] == 1.5
    return Scenario(name="drift_computes", passed=ok, detail=rep)


def _scenario_stale_data_engages_lock(conn: sqlite3.Connection, settings: Settings) -> Scenario:
    _seed_market(conn, "REH")
    # Plant a very old snapshot.
    conn.execute(
        "INSERT INTO market_snapshots (ticker, captured_at, yes_bid, yes_ask) "
        "VALUES ('REH', '1999-01-01T00:00:00+00:00', 40, 42)",
    )
    engaged = gov.check_data_staleness(conn, settings)
    locked = gov.is_locked(conn)
    gov.release_lock(conn, gov.LOCK_STALE_DATA)
    return Scenario(name="stale_data_engages_lock", passed=engaged and locked,
                    detail={"engaged": engaged, "was_locked": locked})


SCENARIOS = (
    _scenario_preview_dry_run,
    _scenario_submit_blocked_without_approval,
    _scenario_duplicate_order_blocked,
    _scenario_governance_lock_blocks_submit,
    _scenario_reconcile_records_fills,
    _scenario_drift_computes,
    _scenario_stale_data_engages_lock,
)


def run_rehearsal(conn: sqlite3.Connection, settings: Settings | None = None) -> RehearsalReport:
    settings = settings or get_settings()
    started = utc_now_iso()
    results: list[Scenario] = []
    for fn in SCENARIOS:
        try:
            results.append(fn(conn, settings))
        except Exception as e:  # noqa: BLE001
            log.warning("scenario_failed", fn=fn.__name__, err=str(e))
            results.append(Scenario(name=fn.__name__, passed=False,
                                    detail={"exception": str(e)}))

    passed = sum(1 for s in results if s.passed)
    failed = len(results) - passed
    rep = RehearsalReport(
        started_at=started, finished_at=utc_now_iso(),
        scenarios=results, passed=passed, failed=failed,
        notes="autonomous rehearsal",
    )
    conn.execute(
        "INSERT INTO rehearsal_runs (started_at, finished_at, scenarios_total, "
        "scenarios_passed, scenarios_failed, audit_json, notes) "
        "VALUES (?,?,?,?,?,?,?)",
        (rep.started_at, rep.finished_at, len(results), passed, failed,
         json.dumps(rep.to_dict()), rep.notes),
    )
    return rep
