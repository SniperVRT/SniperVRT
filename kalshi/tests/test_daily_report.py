from datetime import datetime, timezone

from kalshi_engine.reports.daily import (
    build_daily_report, persist_daily_report, render_daily_report_text,
)


def _seed(conn) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO markets (ticker, title, status, first_seen_at, last_seen_at) "
        "VALUES ('X','t','active',?,?)", (now, now),
    )
    conn.execute(
        "INSERT INTO market_snapshots (ticker, captured_at, yes_bid, yes_ask) "
        "VALUES ('X',?,40,42)", (now,),
    )
    conn.execute(
        "INSERT INTO signals (ticker, side, strategy, fair_prob, implied_prob, edge, "
        "expected_value, confidence, max_price_cents, suggested_size_usd, thesis, "
        "status, created_at) VALUES "
        "('X','yes','news_lag',0.6,0.42,0.18,0.4,0.8,42,5.0,'t','pending',?)", (now,),
    )
    conn.execute(
        "INSERT INTO rejected_signals (ticker, strategy, reason, detail_json, created_at) "
        "VALUES ('X','filter','spread_too_wide','{}',?)", (now,),
    )
    conn.execute(
        "INSERT INTO market_quality (ticker, captured_at, spread_score, liquidity_score, "
        "depth_score, volume_score, freshness_score, tradability_score, rules_score, "
        "total_score, flags_json) VALUES ('X',?,0.8,0.7,0.6,0.7,1.0,0.9,0.8,0.78,'[]')",
        (now,),
    )
    conn.execute(
        "INSERT INTO resolution_classifications (ticker, risk_class, risk_score, "
        "classified_at) VALUES ('X','LOW_RISK_OBJECTIVE',0.1,?)", (now,),
    )


def test_build_and_render_daily_report(conn):
    _seed(conn)
    rep = build_daily_report(conn)
    assert rep["signals_total"] >= 1
    assert rep["scanned_snapshots"] >= 1
    text = render_daily_report_text(rep)
    assert "Daily Report" in text
    assert "signals:" in text


def test_persist_daily_report_upserts(conn):
    _seed(conn)
    rep = build_daily_report(conn)
    persist_daily_report(conn, rep)
    persist_daily_report(conn, rep)  # upsert
    n = conn.execute("SELECT COUNT(*) FROM daily_reports WHERE day=?", (rep["day"],)).fetchone()[0]
    assert n == 1
