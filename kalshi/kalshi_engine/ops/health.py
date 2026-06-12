"""Operational health checks + autonomous emergency shutdowns.

Each check returns `ok | warn | fail`. A `fail` on a critical
component automatically engages the matching governance lock so
no execution can proceed until an operator inspects and clears it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

import structlog

from ..config import Settings, get_settings
from ..db import utc_now_iso
from ..risk import governance as gov

log = structlog.get_logger("ops.health")


@dataclass
class HealthCheck:
    component: str
    status: str        # "ok" | "warn" | "fail"
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class HealthReport:
    captured_at: str
    checks: list[HealthCheck]

    @property
    def overall(self) -> str:
        if any(c.status == "fail" for c in self.checks):
            return "fail"
        if any(c.status == "warn" for c in self.checks):
            return "warn"
        return "ok"

    def to_dict(self) -> dict:
        return {
            "captured_at": self.captured_at,
            "overall": self.overall,
            "checks": [c.to_dict() for c in self.checks],
        }


def _persist_check(conn: sqlite3.Connection, c: HealthCheck) -> None:
    conn.execute(
        "INSERT INTO health_checks (captured_at, component, status, detail) "
        "VALUES (?,?,?,?)",
        (utc_now_iso(), c.component, c.status, c.detail),
    )


# --------------------------------------------------------------------------- #
# Individual checks
# --------------------------------------------------------------------------- #
def check_db(conn: sqlite3.Connection, settings: Settings) -> HealthCheck:
    try:
        conn.execute("SELECT 1").fetchone()
        return HealthCheck("db", "ok")
    except Exception as e:  # noqa: BLE001
        # Best-effort lock engagement — if the DB itself is gone we can't
        # write a lock either, but we still report the failure to caller.
        try:
            gov.engage_lock(conn, gov.LOCK_DB_CORRUPTION, str(e))
        except Exception:  # noqa: BLE001
            pass
        return HealthCheck("db", "fail", str(e))


def check_scanner_freshness(conn: sqlite3.Connection, settings: Settings) -> HealthCheck:
    row = conn.execute(
        "SELECT MAX(captured_at) FROM market_snapshots"
    ).fetchone()
    last = row[0] if row else None
    if not last:
        return HealthCheck("scanner_freshness", "warn", "no_snapshots")
    try:
        ts = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
    except ValueError:
        return HealthCheck("scanner_freshness", "warn", f"unparseable:{last}")
    age_min = (datetime.now(timezone.utc) - ts).total_seconds() / 60.0
    if age_min > settings.max_data_staleness_minutes:
        gov.engage_lock(conn, gov.LOCK_STALE_DATA,
                        f"snapshot age {age_min:.1f} min")
        return HealthCheck("scanner_freshness", "fail",
                           f"age_min={age_min:.1f}")
    if age_min > settings.max_data_staleness_minutes / 2:
        return HealthCheck("scanner_freshness", "warn",
                           f"age_min={age_min:.1f}")
    return HealthCheck("scanner_freshness", "ok",
                       f"age_min={age_min:.1f}")


def check_news_freshness(conn: sqlite3.Connection, settings: Settings) -> HealthCheck:
    row = conn.execute("SELECT MAX(fetched_at) FROM news_evidence").fetchone()
    last = row[0] if row else None
    if not last:
        return HealthCheck("news_freshness", "warn", "no_news")
    try:
        ts = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
    except ValueError:
        return HealthCheck("news_freshness", "warn", f"unparseable:{last}")
    age_min = (datetime.now(timezone.utc) - ts).total_seconds() / 60.0
    if age_min > 120:
        return HealthCheck("news_freshness", "warn", f"age_min={age_min:.0f}")
    return HealthCheck("news_freshness", "ok", f"age_min={age_min:.0f}")


def check_ingestion_runs(conn: sqlite3.Connection, settings: Settings) -> HealthCheck:
    row = conn.execute(
        "SELECT COUNT(*) FROM ingestion_runs WHERE http_errors > 5 "
        "AND started_at >= ?",
        ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),),
    ).fetchone()
    bad = int(row[0] or 0)
    if bad >= 3:
        gov.engage_lock(conn, gov.LOCK_REPEAT_FAILURE,
                        f"{bad} ingestion runs with >5 http errors in 1h")
        return HealthCheck("ingestion_runs", "fail", f"bad={bad}")
    if bad >= 1:
        return HealthCheck("ingestion_runs", "warn", f"bad={bad}")
    return HealthCheck("ingestion_runs", "ok")


def check_open_exposure(conn: sqlite3.Connection, settings: Settings) -> HealthCheck:
    state = gov.compute_state(conn, settings)
    if state.open_exposure_usd > settings.max_open_exposure_usd:
        gov.engage_lock(conn, gov.LOCK_EXPOSURE,
                        f"open=${state.open_exposure_usd:.2f}")
        return HealthCheck("exposure", "fail",
                           f"open=${state.open_exposure_usd:.2f}")
    if state.open_exposure_usd > 0.8 * settings.max_open_exposure_usd:
        return HealthCheck("exposure", "warn",
                           f"open=${state.open_exposure_usd:.2f}")
    return HealthCheck("exposure", "ok",
                       f"open=${state.open_exposure_usd:.2f}")


def check_active_locks(conn: sqlite3.Connection, settings: Settings) -> HealthCheck:
    locks = gov.active_locks(conn)
    if not locks:
        return HealthCheck("locks", "ok")
    return HealthCheck("locks", "fail", f"engaged={[n for n,_ in locks]}")


CHECKS: tuple[Callable[[sqlite3.Connection, Settings], HealthCheck], ...] = (
    check_db,
    check_scanner_freshness,
    check_news_freshness,
    check_ingestion_runs,
    check_open_exposure,
    check_active_locks,
)


def run_health_checks(conn: sqlite3.Connection, settings: Settings | None = None) -> HealthReport:
    settings = settings or get_settings()
    results: list[HealthCheck] = []
    for fn in CHECKS:
        try:
            c = fn(conn, settings)
        except Exception as e:  # noqa: BLE001
            c = HealthCheck(fn.__name__.replace("check_", ""), "fail", str(e))
        results.append(c)
        _persist_check(conn, c)
    return HealthReport(captured_at=utc_now_iso(), checks=results)
