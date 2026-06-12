"""Final autonomous readiness report.

Runs the full test suite, full rehearsal, full health checks, then
emits a single structured JSON + text artifact summarizing system state.

Invoke: `python -m scripts.readiness_report` (run from `kalshi/`).
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _run_pytest() -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--maxfail=1"],
        capture_output=True, text=True,
    )
    return {
        "exit_code": proc.returncode,
        "summary_tail": proc.stdout.splitlines()[-12:],
    }


def _run_rehearsal() -> dict:
    from kalshi_engine.db import connect, init_db
    from kalshi_engine.rehearsal.engine import run_rehearsal
    init_db()
    with connect() as conn:
        rep = run_rehearsal(conn)
    return rep.to_dict()


def _run_health() -> dict:
    from kalshi_engine.db import connect, init_db
    from kalshi_engine.ops.health import run_health_checks
    init_db()
    with connect() as conn:
        rep = run_health_checks(conn)
    return rep.to_dict()


def _governance_status() -> dict:
    from kalshi_engine.config import get_settings
    from kalshi_engine.db import connect
    from kalshi_engine.risk import governance as gov
    settings = get_settings()
    with connect() as conn:
        state = gov.compute_state(conn, settings)
        locks = gov.active_locks(conn)
    return {
        "state": gov.to_dict(state),
        "active_locks": [{"name": n, "reason": r} for n, r in locks],
        "settings": {
            "live_enabled": settings.live_enabled,
            "live_dry_run": settings.live_dry_run,
            "live_require_manual_approval": settings.live_require_manual_approval,
            "execution_mode": settings.execution_mode.value,
        },
    }


def _daily_report() -> dict:
    from kalshi_engine.db import connect
    from kalshi_engine.reports.daily import build_daily_report
    with connect() as conn:
        return build_daily_report(conn)


def _drift_summary() -> dict:
    from kalshi_engine.db import connect
    from kalshi_engine.validation.drift import summary
    with connect() as conn:
        return summary(conn)


def main() -> int:
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pytest": _run_pytest(),
        "rehearsal": _run_rehearsal(),
        "health": _run_health(),
        "governance": _governance_status(),
        "daily": _daily_report(),
        "drift": _drift_summary(),
    }

    # Compute live-readiness verdict.
    health_ok = report["health"]["overall"] == "ok"
    no_locks = not report["governance"]["active_locks"]
    pytest_ok = report["pytest"]["exit_code"] == 0
    rehearsal_ok = report["rehearsal"]["failed"] == 0
    s = report["governance"]["settings"]
    safety = s["live_require_manual_approval"]
    live_gate_explicit = s["live_enabled"] and not s["live_dry_run"]

    if pytest_ok and rehearsal_ok and health_ok and no_locks and safety and live_gate_explicit:
        verdict = "LIVE_READY"
    else:
        verdict = "LIVE_LOCKED"
    report["verdict"] = verdict
    report["blockers"] = [
        b for b in (
            None if pytest_ok else "pytest_failed",
            None if rehearsal_ok else "rehearsal_failed",
            None if health_ok else f"health_{report['health']['overall']}",
            None if no_locks else f"active_locks:{[l['name'] for l in report['governance']['active_locks']]}",
            None if safety else "manual_approval_disabled",
            None if live_gate_explicit else "live_disabled_or_dry_run",
        ) if b
    ]

    out_dir = Path("./data")
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "readiness_report.json"
    json_path.write_text(json.dumps(report, indent=2, default=str))

    print(f"verdict: {verdict}")
    print(f"blockers: {report['blockers'] or 'none'}")
    print(f"pytest exit={report['pytest']['exit_code']}")
    print(f"rehearsal {report['rehearsal']['passed']}/{report['rehearsal']['passed']+report['rehearsal']['failed']} passed")
    print(f"health: {report['health']['overall']}")
    print(f"locks: {[l['name'] for l in report['governance']['active_locks']] or 'none'}")
    print(f"signals today: {report['daily']['signals_total']}  open paper: {report['daily']['open_paper_positions']}")
    print(f"written: {json_path}")
    return 0 if verdict == "LIVE_READY" else 1


if __name__ == "__main__":
    sys.exit(main())
