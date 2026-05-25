from backend.app.backtest.engine import run_backtest
from backend.app.data import ensure_dataset, load_candles
from backend.app.strategies import build_strategy


def test_backtest_runs_and_returns_metrics():
    ensure_dataset(limit=1500)
    df = load_candles(limit=1500)
    strat = build_strategy("ema_trend")
    result = run_backtest(strat, df)
    d = result.to_dict()
    for key in ("starting_equity", "final_equity", "sharpe", "max_drawdown_pct",
                "win_rate", "profit_factor", "num_trades", "equity_curve", "trades"):
        assert key in d
    assert len(d["equity_curve"]) == len(df)
    # Sanity: no negative equity (strategy is long-only-or-short on cash, no leverage)
    assert all(p["equity"] >= -1.0 for p in d["equity_curve"])


def test_backtest_zero_data_safe():
    import pandas as pd
    empty = pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    result = run_backtest(build_strategy("ema_trend"), empty)
    assert result.num_trades == 0
    assert result.final_equity == result.starting_equity
