from datetime import datetime, timezone

from kalshi_engine.risk import governance as gov


def test_locks_engage_and_release(conn):
    gov.engage_lock(conn, gov.LOCK_EMERGENCY, "test")
    assert gov.is_locked(conn)
    gov.release_lock(conn, gov.LOCK_EMERGENCY)
    assert not gov.is_locked(conn)


def test_evaluate_blocks_when_locked(conn, settings):
    gov.engage_lock(conn, gov.LOCK_DRAWDOWN, "test")
    d = gov.evaluate_portfolio(conn=conn, settings=settings,
                               proposed_size_usd=1.0)
    assert not d.allowed and d.reason == "locked"
    gov.release_lock(conn, gov.LOCK_DRAWDOWN)


def test_exposure_cap_blocks(conn, settings):
    # Push exposure to the cap.
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO paper_positions (ticker, side, qty, avg_cost_cents, opened_at, strategy) "
        "VALUES ('A','yes',100,15,?,'news_lag')", (now,),
    )
    d = gov.evaluate_portfolio(conn=conn, settings=settings,
                               proposed_size_usd=5.0, category="Economics",
                               strategy="news_lag")
    assert not d.allowed and d.reason == "open_exposure_cap"


def test_category_cap_blocks(conn, settings):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO markets (ticker, title, status, category, first_seen_at, last_seen_at) "
        "VALUES ('A','t','active','Weather',?,?)", (now, now),
    )
    conn.execute(
        "INSERT INTO paper_positions (ticker, side, qty, avg_cost_cents, opened_at, strategy) "
        "VALUES ('A','yes',50,18,?,'news_lag')", (now,),
    )
    d = gov.evaluate_portfolio(conn=conn, settings=settings,
                               proposed_size_usd=2.0, category="Weather")
    assert not d.allowed and d.reason in ("open_exposure_cap", "category_exposure_cap")


def test_stale_data_engages_lock(conn, settings):
    conn.execute(
        "INSERT INTO markets (ticker, title, status, first_seen_at, last_seen_at) "
        "VALUES ('A', 't', 'active', 't', 't')"
    )
    conn.execute(
        "INSERT INTO market_snapshots (ticker, captured_at, yes_bid, yes_ask) "
        "VALUES ('A', '1999-01-01T00:00:00+00:00', 40, 42)"
    )
    assert gov.check_data_staleness(conn, settings) is True
    assert gov.is_locked(conn)
    gov.release_lock(conn, gov.LOCK_STALE_DATA)


def test_consecutive_losses_engages_lock(conn, settings):
    now = datetime.now(timezone.utc).isoformat()
    for i in range(settings.max_consecutive_losses + 1):
        conn.execute(
            "INSERT INTO paper_positions (ticker, side, qty, avg_cost_cents, "
            "opened_at, closed_at, realized_pnl_usd, strategy) VALUES "
            "(?, 'yes', 1, 40, ?, ?, -1.0, 'news_lag')",
            (f"L{i}", now, now),
        )
    d = gov.evaluate_portfolio(conn=conn, settings=settings, proposed_size_usd=1.0)
    assert not d.allowed and d.reason == "consecutive_losses"


def test_clean_path_allows(conn, settings):
    d = gov.evaluate_portfolio(conn=conn, settings=settings, proposed_size_usd=1.0)
    assert d.allowed
