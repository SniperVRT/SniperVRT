# Architecture

## Layers

```
┌──────────────────────────────────────────────────────────────┐
│  Frontend SPA (Vue 3 + Chart.js)                              │
│  Served by FastAPI as static files at /                       │
└──────────────────────────────────────────────────────────────┘
                                ▲
                                │ JSON REST
                                ▼
┌──────────────────────────────────────────────────────────────┐
│  FastAPI API layer (backend/app/api/router.py)                │
│  Health, data, strategies, backtest, paper, risk, governance, │
│  learning, live (locked), events, reports, settings.          │
└──────────────────────────────────────────────────────────────┘
                                ▲
                                │
        ┌───────────────────────┼────────────────────────┐
        ▼                       ▼                        ▼
┌────────────────┐    ┌─────────────────┐     ┌────────────────────┐
│ Strategy layer │    │ Risk engine     │     │ Paper-trade runtime│
│ EMA, RSI, BO,  │    │ Position size,  │     │ Background thread, │
│ vol regime,    │    │ stops, daily    │     │ ticks each N sec,  │
│ ensemble       │    │ loss, dd, vol,  │     │ persists every     │
│ adaptive wts   │    │ data quality,   │     │ decision           │
└───────┬────────┘    │ confidence,     │     └─────────┬──────────┘
        │             │ kill switch     │               │
        │             └─────────────────┘               │
        ▼                                                ▼
┌────────────────────────────────────────────────────────────────┐
│  Backtest engine (event-driven, no look-ahead)                  │
│  Same indicators, same risk model, same execution simulator     │
│  → identical logic between research and paper                    │
└────────────────────────────────────────────────────────────────┘
                                ▲
                                │
                                ▼
┌────────────────────────────────────────────────────────────────┐
│  Execution simulator (fees, spread, slippage, fill quality)     │
└────────────────────────────────────────────────────────────────┘
                                ▲
                                │
                                ▼
┌────────────────────────────────────────────────────────────────┐
│  Data layer (CCXT live or synthetic fallback)                   │
│  Persists to candles table, validates duplicates / gaps         │
└────────────────────────────────────────────────────────────────┘
                                ▲
                                │
                                ▼
┌────────────────────────────────────────────────────────────────┐
│  SQLite via SQLAlchemy (WAL mode, busy_timeout, pool_pre_ping)  │
│  Tables: candles, strategy_configs, backtest_runs, positions,   │
│  trades, event_log, risk_blocks, learning_runs, paper_accounts, │
│  governance_decisions                                            │
└────────────────────────────────────────────────────────────────┘
```

## Key design choices

- **Cash-margin PnL accounting.** Both the backtester and the paper runtime
  track equity as `cash + unrealized PnL of open position`. Opening a position
  charges only the fee; closing books realized PnL to cash. This keeps backtest
  and paper math identical.

- **No look-ahead.** All indicators are causal pandas operations
  (`ewm`, rolling rank, ATR built from prev-close TR). The backtest engine
  applies stops/take-profits using the current bar's high/low, then evaluates
  new signals using the bar that just closed.

- **Risk before action.** Every decision — paper, backtest, future live — runs
  through `RiskEngine.evaluate`. It enforces position sizing, max open
  positions, daily loss, drawdown, consecutive-loss cooldowns, volatility
  shutdown, data quality, confidence floor, and kill switch.

- **Live gate is the chokepoint.** No code in this build places live orders.
  The `LiveGate` composes four independent locks; a future `LiveExecutor` would
  ask `gate.is_unlocked()` before sending anything, and that's where the safety
  contract lives.

- **Deterministic synthetic data.** When the exchange is unreachable, a
  GBM-style BTC series is generated on a fixed timeframe grid and persisted.
  Subsequent refreshes *advance* the series forward in time, so the paper
  runtime continues to see new candles.

- **Threaded paper loop.** The paper runtime runs in a daemon `Thread` so the
  FastAPI request path stays sync and SQLite stays happy. WAL mode +
  busy_timeout=30s handle the rare concurrent writes between API requests and
  the tick loop.

- **All persistence in one DB.** Crashes are recoverable: paper account,
  positions, trades, event log, and learning runs all survive a restart. The
  status endpoint always reflects current state.

## Sequence: a paper-trade decision

```
1. paper loop calls tick_once() every paper.tick_seconds
2. ensure_dataset()        → fetch new candles or advance synthetic series
3. load_candles()          → DataFrame indexed by time
4. strategy.signal_now(df) → Signal(side, confidence, stop_pct, tp_pct)
5. For each open position: check stop / take-profit / signal flip → maybe close
6. risk.evaluate(...)      → RiskCheck (allowed?, size, stop, tp, blocks[])
7. If allowed and no open position: open position via simulate_fill
8. Mark-to-market: acc.equity = cash + unrealized; update peak / daily anchor
9. Persist tick event to event_log; risk blocks (if any) to risk_blocks
```

Every step is observable: status snapshots, event log, risk block log,
governance scorecard.
