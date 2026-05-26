def test_schema_has_expected_tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r[0] for r in rows}
    for required in (
        "markets", "market_snapshots", "news_items", "source_snapshots",
        "probability_estimates", "signals", "rejected_signals",
        "trades", "positions", "risk_events", "pnl_history",
        "resolutions", "model_performance",
    ):
        assert required in names, f"missing table {required}"


def test_signals_status_constraint(conn):
    import pytest
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO signals (ticker, side, strategy, fair_prob, implied_prob, "
            "edge, expected_value, confidence, max_price_cents, suggested_size_usd, "
            "thesis, status, created_at) VALUES "
            "('X','yes','s',0.5,0.4,0.1,0.05,0.7,42,1.0,'t','bogus','2025-01-01T00:00:00Z')"
        )


def test_round_trip_signal_insert(conn):
    conn.execute(
        "INSERT INTO markets (ticker, title, status, first_seen_at, last_seen_at) "
        "VALUES ('K1','t','active','2025-01-01T00:00:00Z','2025-01-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO signals (ticker, side, strategy, fair_prob, implied_prob, "
        "edge, expected_value, confidence, max_price_cents, suggested_size_usd, "
        "thesis, status, created_at) VALUES "
        "('K1','yes','news_lag',0.55,0.42,0.13,0.12,0.7,42,1.0,'t','pending','2025-01-01T00:00:00Z')"
    )
    row = conn.execute("SELECT side, edge FROM signals WHERE ticker='K1'").fetchone()
    assert row["side"] == "yes"
    assert abs(row["edge"] - 0.13) < 1e-9
