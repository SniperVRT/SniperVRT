from datetime import datetime, timezone

from kalshi_engine.ops.health import run_health_checks
from kalshi_engine.risk import governance as gov
from kalshi_engine.validation.drift import compute_drift, summary as drift_summary


def test_compute_drift_persists_and_returns_slippage(conn):
    r = compute_drift(conn, expected_cents=42.0, actual_cents=44.5,
                      expected_spread_cents=2, realized_spread_cents=3,
                      ticker="X", execution_delay_ms=85)
    assert r["slippage_cents"] == 2.5
    n = conn.execute("SELECT COUNT(*) FROM drift_reports").fetchone()[0]
    assert n == 1


def test_drift_summary_aggregates(conn):
    for actual in (43, 44, 45):
        compute_drift(conn, expected_cents=42, actual_cents=actual,
                      expected_spread_cents=2, realized_spread_cents=3,
                      ticker="X")
    s = drift_summary(conn)
    assert s["n"] == 3
    assert s["max_slippage_cents"] == 3
    assert s["min_slippage_cents"] == 1


def test_drift_summary_empty(conn):
    s = drift_summary(conn)
    assert s["n"] == 0


def _seed_market(conn, ticker="X"):
    conn.execute(
        "INSERT OR IGNORE INTO markets (ticker, title, status, first_seen_at, last_seen_at) "
        "VALUES (?, 't', 'active', 't', 't')", (ticker,),
    )


def test_health_returns_overall_ok(conn, settings):
    _seed_market(conn)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO market_snapshots (ticker, captured_at, yes_bid, yes_ask) "
        "VALUES ('X', ?, 40, 42)", (now,),
    )
    rep = run_health_checks(conn, settings)
    components = {c.component for c in rep.checks}
    assert "db" in components and "scanner_freshness" in components


def test_health_engages_stale_data_lock(conn, settings):
    _seed_market(conn)
    conn.execute(
        "INSERT INTO market_snapshots (ticker, captured_at, yes_bid, yes_ask) "
        "VALUES ('X', '1999-01-01T00:00:00+00:00', 40, 42)"
    )
    rep = run_health_checks(conn, settings)
    assert rep.overall == "fail"
    assert gov.is_locked(conn)
    gov.release_lock(conn, gov.LOCK_STALE_DATA)


def test_health_db_check_handles_closed_connection(conn, settings):
    from kalshi_engine.ops.health import check_db
    conn.close()
    res = check_db(conn, settings)
    # Connection closed -> the check should report `fail` rather than crash.
    assert res.status == "fail"
