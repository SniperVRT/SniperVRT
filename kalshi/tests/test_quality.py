from datetime import datetime, timedelta, timezone

from kalshi_engine.connectors.kalshi import Market, Orderbook
from kalshi_engine.core.quality import score_market, persist


def _mk(**o) -> Market:
    base = dict(
        ticker="X", event_ticker=None, series_ticker=None,
        title="t", subtitle="", category="economics", status="active",
        yes_bid=40, yes_ask=42, no_bid=58, no_ask=60,
        last_price=41, volume=1000, volume_24h=2000, open_interest=500,
        liquidity=10000,
        close_time=datetime.now(timezone.utc) + timedelta(hours=4),
        open_time=None, expected_expiration_time=None,
        rules_primary="Resolves YES if X > Y by 12:00 ET per official source.",
        rules_secondary=None, settlement_source="official", raw={},
    )
    base.update(o)
    return Market(**base)


def test_clean_market_scores_well():
    q = score_market(_mk())
    assert 0.5 < q.total <= 1.0
    assert "wide_spread" not in q.flags


def test_wide_spread_flagged_and_lower_total():
    q = score_market(_mk(yes_bid=10, yes_ask=90))
    assert "wide_spread" in q.flags
    assert q.spread == 0.0


def test_thin_volume_flagged():
    q = score_market(_mk(volume_24h=5))
    assert "low_volume" in q.flags


def test_vague_rules_penalised():
    q = score_market(_mk(rules_primary="Resolves YES if approximately many."))
    assert q.rules < 0.4
    assert "rules_unclear" in q.flags


def test_depth_score_uses_book_when_present():
    book = Orderbook(ticker="X", yes=[(42, 200), (41, 100)], no=[(58, 200), (59, 100)])
    with_book = score_market(_mk(), book=book)
    without = score_market(_mk(), book=None)
    assert with_book.depth >= without.depth


def test_persist_quality(conn):
    q = score_market(_mk())
    conn.execute(
        "INSERT INTO markets (ticker, title, status, first_seen_at, last_seen_at) "
        "VALUES ('X','t','active','t','t')"
    )
    persist(conn, "X", q)
    row = conn.execute("SELECT total_score, flags_json FROM market_quality WHERE ticker='X'").fetchone()
    assert abs(float(row[0]) - q.total) < 1e-9
