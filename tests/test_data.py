from backend.app.data import ensure_dataset, load_candles, data_quality, latest_price


def test_synthetic_dataset_is_persisted():
    res = ensure_dataset(limit=600)
    assert res["ok"]
    # First call inserts; idempotent re-calls insert 0 — both are valid outcomes.
    df = load_candles(limit=500)
    assert not df.empty
    assert {"open", "high", "low", "close", "volume"}.issubset(df.columns)


def test_data_quality_metrics():
    ensure_dataset(limit=500)
    dq = data_quality()
    assert dq.candle_count > 0
    assert 0.0 <= dq.coverage_pct <= 1.0
    assert dq.duplicate_candles == 0
    assert latest_price() is not None
