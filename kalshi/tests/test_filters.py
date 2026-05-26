from datetime import datetime, timedelta, timezone

from kalshi_engine.connectors.kalshi import Market
from kalshi_engine.core import filters as F


def _mk(**overrides) -> Market:
    base = dict(
        ticker="X", event_ticker=None, series_ticker=None,
        title="t", subtitle="", category="economics", status="active",
        yes_bid=40, yes_ask=42, no_bid=58, no_ask=60,
        last_price=41, volume=1000, volume_24h=2000, open_interest=500,
        liquidity=10000,
        close_time=datetime.now(timezone.utc) + timedelta(hours=4),
        open_time=None, expected_expiration_time=None,
        rules_primary="Resolves YES if X is above Y as reported by official source.",
        rules_secondary=None,
        settlement_source="official",
        raw={},
    )
    base.update(overrides)
    return Market(**base)


def test_passes_clean_market(settings):
    assert F.evaluate(_mk(), settings).passed


def test_blocks_wide_spread(settings):
    res = F.evaluate(_mk(yes_bid=30, yes_ask=50), settings)
    assert not res.passed and res.reason == "spread_too_wide"


def test_blocks_low_volume(settings):
    res = F.evaluate(_mk(volume_24h=10), settings)
    assert not res.passed and res.reason == "low_volume_24h"


def test_blocks_too_close_to_close(settings):
    res = F.evaluate(_mk(close_time=datetime.now(timezone.utc) + timedelta(minutes=2)), settings)
    assert not res.passed and res.reason == "too_close_to_expiry"


def test_blocks_blocked_category(settings):
    res = F.evaluate(_mk(category="Sports - NFL"), settings)
    assert not res.passed and res.reason == "blocked_category"


def test_blocks_vague_rules(settings):
    res = F.evaluate(_mk(rules_primary="Resolves YES if approximately many people."), settings)
    assert not res.passed and res.reason == "rules_vague"
