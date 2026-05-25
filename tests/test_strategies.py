import pandas as pd
import pytest

from backend.app.data import ensure_dataset, load_candles
from backend.app.strategies import STRATEGY_REGISTRY, build_strategy, detect_regime


@pytest.fixture(scope="module")
def df():
    ensure_dataset(limit=1500)
    return load_candles(limit=1500)


@pytest.mark.parametrize("name", list(STRATEGY_REGISTRY.keys()))
def test_each_strategy_generates_signals(name, df):
    strat = build_strategy(name)
    out = strat.generate(df)
    assert "signal" in out.columns
    assert out["signal"].isin([-1, 0, 1]).all()
    sig = strat.signal_now(df)
    assert sig.side in ("long", "flat", "short")
    assert 0.0 <= sig.confidence <= 1.0


def test_regime_detection(df):
    r = detect_regime(df)
    assert r["regime"] in ("bull", "bear", "chop", "high_vol", "unknown")
