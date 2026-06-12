from datetime import datetime, timedelta, timezone

from kalshi_engine.connectors.kalshi import Market
from kalshi_engine.core.probability import FairEstimate
from kalshi_engine.core.signals import Rejection, Signal, build_signal, rank


def _mk(**overrides) -> Market:
    base = dict(
        ticker="X", event_ticker=None, series_ticker=None,
        title="t", subtitle="", category="economics", status="active",
        yes_bid=40, yes_ask=42, no_bid=58, no_ask=60,
        last_price=41, volume=1000, volume_24h=2000, open_interest=500,
        liquidity=10000,
        close_time=datetime.now(timezone.utc) + timedelta(hours=4),
        open_time=None, expected_expiration_time=None,
        rules_primary="r", rules_secondary=None, settlement_source="x", raw={},
    )
    base.update(overrides)
    return Market(**base)


def test_build_signal_happy_path(settings):
    fair = FairEstimate(0.60, 0.75, 0.55, 0.65)  # band excludes 0.42
    out = build_signal(market=_mk(), fair=fair, strategy="news_lag", settings=settings)
    assert isinstance(out, Signal)
    assert out.side == "yes"
    assert out.edge >= settings.min_edge
    assert out.suggested_size_usd <= settings.max_position_usd


def test_rejects_low_edge(settings):
    fair = FairEstimate(0.44, 0.8, 0.42, 0.46)
    out = build_signal(market=_mk(), fair=fair, strategy="news_lag", settings=settings)
    assert isinstance(out, Rejection)
    assert out.reason == "edge_too_small"


def test_rejects_low_confidence(settings):
    fair = FairEstimate(0.60, 0.20, 0.55, 0.65)
    out = build_signal(market=_mk(), fair=fair, strategy="news_lag", settings=settings)
    assert isinstance(out, Rejection)
    assert out.reason == "low_confidence"


def test_rejects_when_band_overlaps_market(settings):
    fair = FairEstimate(0.55, 0.8, 0.35, 0.70)  # straddles 0.42 YES ask
    out = build_signal(market=_mk(), fair=fair, strategy="news_lag", settings=settings)
    assert isinstance(out, Rejection)
    assert out.reason == "uncertainty_overlaps_market"


def test_rank_orders_by_edge_x_confidence_x_ev():
    s1 = Signal("A", "yes", "x", 0.6, 0.4, 0.20, 0.18, 0.9, 40, 5.0, "t")
    s2 = Signal("B", "yes", "x", 0.6, 0.4, 0.10, 0.05, 0.6, 40, 5.0, "t")
    assert rank([s2, s1])[0].ticker == "A"
