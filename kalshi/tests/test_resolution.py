from datetime import datetime, timedelta, timezone

from kalshi_engine.connectors.kalshi import Market
from kalshi_engine.core.resolution import RiskClass, classify, is_tradeable, persist


def _mk(**o) -> Market:
    base = dict(
        ticker="X", event_ticker=None, series_ticker=None,
        title="Will CPI YoY be above 3%?", subtitle="", category="Economics", status="active",
        yes_bid=40, yes_ask=42, no_bid=58, no_ask=60,
        last_price=41, volume=1000, volume_24h=2000, open_interest=500, liquidity=10000,
        close_time=datetime.now(timezone.utc) + timedelta(hours=4),
        open_time=None, expected_expiration_time=None,
        rules_primary=(
            "Resolves YES if BLS CPI YoY is at or above 3.0% as reported by "
            "the official BLS release by 09:00 ET on 2026-06-12."
        ),
        rules_secondary=None,
        settlement_source="BLS",
        raw={},
    )
    base.update(o)
    return Market(**base)


def test_objective_rule_classified_low_risk():
    c = classify(_mk())
    assert c.risk_class == RiskClass.LOW
    assert is_tradeable(c)
    assert c.source_name == "BLS"


def test_subjective_rule_rejected():
    c = classify(_mk(rules_primary="Resolves YES if in the opinion of the issuer X is great."))
    assert c.risk_class == RiskClass.REJECT_SUBJECTIVE
    assert not is_tradeable(c)


def test_unclear_source_rejected():
    c = classify(_mk(
        rules_primary="Resolves YES if X happens.",
        settlement_source=None,
    ))
    assert c.risk_class in (RiskClass.REJECT_SOURCE_UNCLEAR, RiskClass.HIGH)


def test_vague_language_medium_or_high():
    c = classify(_mk(rules_primary="Resolves YES if approximately many people show up."))
    assert c.risk_class in (RiskClass.MEDIUM, RiskClass.HIGH, RiskClass.REJECT_SOURCE_UNCLEAR)


def test_persist_upserts(conn):
    conn.execute(
        "INSERT INTO markets (ticker, title, status, first_seen_at, last_seen_at) "
        "VALUES ('X','t','active','t','t')"
    )
    c = classify(_mk())
    persist(conn, c)
    persist(conn, c)  # upsert
    n = conn.execute("SELECT COUNT(*) FROM resolution_classifications WHERE ticker='X'").fetchone()[0]
    assert n == 1
