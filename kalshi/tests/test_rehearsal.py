from kalshi_engine.rehearsal.engine import run_rehearsal
from kalshi_engine.risk import governance as gov


def test_rehearsal_runs_all_scenarios(conn, settings):
    rep = run_rehearsal(conn, settings)
    assert len(rep.scenarios) >= 5
    # Persisted in rehearsal_runs
    row = conn.execute(
        "SELECT scenarios_total, scenarios_passed FROM rehearsal_runs"
    ).fetchone()
    assert row is not None
    assert int(row[0]) == len(rep.scenarios)


def test_rehearsal_does_not_leave_locks_engaged(conn, settings):
    # Scenarios may engage locks transiently; the engine should clean up.
    run_rehearsal(conn, settings)
    # The duplicate-order scenario releases LOCK_DUPLICATE_ORDER; stale-data
    # scenario releases LOCK_STALE_DATA. No other lock should be set.
    active = [n for n, _ in gov.active_locks(conn)]
    assert active == [], f"unexpected locks: {active}"


def test_rehearsal_scenarios_pass_default_environment(conn, settings):
    rep = run_rehearsal(conn, settings)
    failed = [s.name for s in rep.scenarios if not s.passed]
    # In the default test environment all scenarios should pass.
    assert failed == [], failed
