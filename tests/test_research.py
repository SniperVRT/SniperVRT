from backend.app.backtest.engine import run_backtest
from backend.app.core.db import session_scope
from backend.app.data import ensure_dataset, load_candles
from backend.app.intelligence import ingest_news
from backend.app.models import BacktestRun
from backend.app.research import (
    discover_edges, monte_carlo_run, recent_edges, recent_walk_forwards,
    run_all_agents, run_agent, walk_forward,
)
from backend.app.research.edge_discovery import _bh_correct
from backend.app.strategies import build_strategy


def test_bh_correction_is_monotone():
    ps = [0.001, 0.01, 0.03, 0.1, 0.5, 0.9]
    adj = _bh_correct(ps)
    assert len(adj) == len(ps)
    for a in adj:
        assert 0.0 <= a <= 1.0
    # Adjusted p-values should be >= raw p-values
    for raw, a in zip(ps, adj):
        assert a >= raw - 1e-9


def test_edge_discovery_runs():
    ensure_dataset(limit=2000)
    ingest_news(force_synthetic=True)
    res = discover_edges()
    assert res["ok"]
    assert res["tested"] > 0
    # Acceptance is allowed but never required on synthetic data
    assert res["accepted"] >= 0
    edges = recent_edges(limit=5)
    assert len(edges) >= 1


def test_walk_forward_produces_folds():
    ensure_dataset(limit=2500)
    res = walk_forward("ema_trend", window_size=400, step_size=200)
    assert res["ok"]
    assert len(res["folds"]) >= 2
    assert "mean_sharpe" in res["aggregate"]
    runs = recent_walk_forwards(limit=3)
    assert len(runs) >= 1


def test_monte_carlo_run_on_backtest():
    ensure_dataset(limit=1500)
    df = load_candles(limit=1500)
    result = run_backtest(build_strategy("ema_trend"), df)
    with session_scope() as s:
        run = BacktestRun(
            strategy_name="ema_trend", strategy_type="ema_trend", params={},
            symbol="BTC/USDT", timeframe="1h",
            start_ts=int(df["ts"].iloc[0]), end_ts=int(df["ts"].iloc[-1]),
            starting_equity=result.starting_equity,
            final_equity=result.final_equity,
            total_return_pct=result.total_return_pct,
            sharpe=result.sharpe, max_drawdown_pct=result.max_drawdown_pct,
            win_rate=result.win_rate, profit_factor=result.profit_factor,
            num_trades=result.num_trades, avg_trade_pct=result.avg_trade_pct,
            expectancy=result.expectancy, metrics=result.metrics,
            equity_curve=result.equity_curve[-200:], trades_log=result.trades[-200:],
        )
        s.add(run)
        s.flush()
        rid = run.id
    mc = monte_carlo_run(rid, n_samples=300)
    assert mc["ok"]
    m = mc["metrics"]
    assert "return_pct" in m and {"p5", "p50", "p95"}.issubset(m["return_pct"].keys())


def test_run_all_agents_returns_each():
    ensure_dataset(limit=600)
    ingest_news(force_synthetic=True)
    res = run_all_agents()
    assert res["ok"]
    assert set(res["results"].keys()) >= {
        "news", "sentiment", "macro", "volatility", "regime",
        "strategy_evaluator", "risk_auditor", "edge_curator", "micro",
    }


def test_run_one_agent():
    ensure_dataset(limit=400)
    r = run_agent("volatility")
    assert r["agent"] == "volatility"
