# Next steps

The platform is fully functional for research, backtesting, paper trading,
governance, and reporting. The highest-priority extensions:

## Tier 1 — research depth
- **Walk-forward testing.** Backtest each strategy on rolling windows and
  validate out-of-sample to detect overfitting.
- **Monte Carlo on trades.** Resample trade sequences to estimate confidence
  intervals on Sharpe and max DD.
- **Regime-conditional metrics.** Split the strategy scoreboard by detected
  regime (bull, bear, chop, high_vol).

## Tier 2 — robustness
- **Multi-asset and multi-timeframe.** The data layer and models already
  support arbitrary symbols / timeframes; expose this in the UI.
- **Postgres backend.** SQLite is fine for one box; switch to Postgres for
  multi-process deployments.
- **Structured trace IDs on event_log.** Group ticks → decisions → trades by a
  shared `trace_id` for forensic analysis.

## Tier 3 — live execution (only after governance score ≥ 80)
- **LiveExecutor module** that gates every order through
  `LiveGate.is_unlocked()` and routes through CCXT.
- **Live/paper drift monitor.** Run paper and live side-by-side; alert when
  realized PnL diverges by more than N bps.
- **Position reconciliation.** Reconcile DB positions with exchange state at
  every tick.

## Tier 4 — adaptive learning
- **Bayesian optimization** instead of grid sweeps for parameter tuning.
- **Failure memory.** Persist parameter sets that performed worse than a
  threshold; have the tournament avoid re-trying them.
- **Online ensemble weights.** Update ensemble weights from recent paper
  performance, not just backtest rolling Sharpe.

## Tier 5 — UI polish
- **WebSocket push** for tick updates instead of 4-second polling.
- **Drawdown heatmaps and monthly performance tables** in the Reports tab.
- **Per-strategy parameter editor** with inline backtest preview.
