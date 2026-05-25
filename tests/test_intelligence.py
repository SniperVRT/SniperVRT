from backend.app.data import ensure_dataset
from backend.app.intelligence import (
    collect_micro_signals, detect_regime_v2, ingest_news, snapshot_sentiment,
    recent_micro, recent_news, recent_regimes, recent_sentiment,
)
from backend.app.intelligence.news import attribute_market_reactions


def test_news_ingest_offline_falls_back_to_synthetic():
    res = ingest_news(force_synthetic=True)
    assert res["ok"]
    assert res["fetched"] > 0
    # First call inserts; second is dedup-idempotent
    res2 = ingest_news(force_synthetic=True)
    assert res2["inserted"] == 0
    items = recent_news(limit=20)
    assert len(items) > 0
    for it in items:
        assert it["topic"] in {"btc", "macro", "regulation", "etf", "hack",
                                "exchange", "miner", "liquidation",
                                "institution", "onchain"}
        assert -1.0 <= it["sentiment"] <= 1.0
        assert 0.0 <= it["importance"] <= 1.0


def test_news_attribute_handles_missing_future():
    ingest_news(force_synthetic=True)
    res = attribute_market_reactions()
    assert res["ok"]


def test_sentiment_snapshot_records():
    ingest_news(force_synthetic=True)
    res = snapshot_sentiment()
    assert res["ok"]
    assert any(s["source"] == "news_agg" for s in res["snapshots"])
    snaps = recent_sentiment(limit=5)
    assert len(snaps) >= 1


def test_micro_signals_computed():
    ensure_dataset(limit=800)
    res = collect_micro_signals()
    assert res["ok"]
    assert res["count"] >= 5
    sigs = recent_micro(limit=20)
    names = {s["name"] for s in sigs}
    assert {"vol_zscore", "atr_zscore", "volume_zscore"}.issubset(names)


def test_regime_v2_classifies():
    ensure_dataset(limit=800)
    r = detect_regime_v2()
    assert r["regime"] in {
        "trend_up", "trend_down", "range_chop", "compression",
        "expansion", "panic_down", "euphoric_up", "post_shock", "unknown",
    }
    hist = recent_regimes(limit=5)
    assert len(hist) >= 1
