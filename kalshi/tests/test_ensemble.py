from kalshi_engine.core.ensemble import StrategyVote, combine, persist
from kalshi_engine.core.probability import FairEstimate


def test_combines_agreeing_strategies():
    votes = [
        StrategyVote("news_lag", FairEstimate(0.62, 0.7, 0.58, 0.66)),
        StrategyVote("rules_mispricing", FairEstimate(0.60, 0.7, 0.55, 0.65)),
    ]
    r = combine(ticker="X", votes=votes, yes_bid=40, yes_ask=42, no_bid=58, no_ask=60)
    assert r is not None
    assert 0.55 < r.combined_fair < 0.65
    assert r.agreement > 0.9
    assert r.recommendation == "yes"


def test_disagreement_lowers_confidence_and_blocks():
    votes = [
        StrategyVote("news_lag", FairEstimate(0.70, 0.7, 0.65, 0.75)),
        StrategyVote("mean_reversion", FairEstimate(0.30, 0.7, 0.25, 0.35)),
    ]
    r = combine(ticker="X", votes=votes, yes_bid=40, yes_ask=42, no_bid=58, no_ask=60)
    assert r is not None
    assert r.agreement < 0.7
    assert "low_agreement" in r.flags


def test_hold_when_band_overlaps_market():
    votes = [
        StrategyVote("rules_mispricing", FairEstimate(0.45, 0.7, 0.30, 0.55)),
    ]
    r = combine(ticker="X", votes=votes, yes_bid=40, yes_ask=42, no_bid=58, no_ask=60)
    assert r is not None
    assert r.recommendation == "hold"


def test_persist_writes_row(conn):
    votes = [StrategyVote("news_lag", FairEstimate(0.6, 0.7, 0.55, 0.65))]
    r = combine(ticker="X", votes=votes, yes_bid=40, yes_ask=42, no_bid=58, no_ask=60)
    conn.execute(
        "INSERT INTO markets (ticker, title, status, first_seen_at, last_seen_at) "
        "VALUES ('X','t','active','t','t')"
    )
    eid = persist(conn, r)
    row = conn.execute("SELECT combined_fair, recommendation FROM ensemble_votes WHERE id=?",
                       (eid,)).fetchone()
    assert abs(float(row[0]) - r.combined_fair) < 1e-9
    assert row[1] == r.recommendation
