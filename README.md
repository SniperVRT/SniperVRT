# SniperVRT — BTC AI Trading Platform

A modular, professional-grade research → backtest → paper-trade → governance →
(locked) live BTC trading platform. Built around hard safety primitives: live
trading is **locked by default** behind four independent gates, every order
passes through a strict risk engine, and every strategy decision is logged.

This is not a prototype, not a hype dashboard, and not a black box. It actually
runs, persists state, executes paper trades, generates reports, and answers an
honest question: *would the system trust itself to go live yet?*

---

## What's inside

```
backend/        FastAPI app, models, strategies, risk, execution, paper, governance
frontend/       Single-page Vue 3 + Chart.js UI (no build step, served by FastAPI)
configs/        YAML config: data, risk, execution, live (locked by default)
data/           Persisted market data
runtime_state/  SQLite DB, paper account, positions, trades
reports/        Council decisions, readiness reports, paper summaries
logs/           Structured platform logs
scripts/        One-command startup, backtest, tournament, validation
docker/         Dockerfile + docker-compose
tests/          Pytest suite covering data, strategies, risk, backtest, paper, API
```

## Quick start

```bash
# 1. Install Python deps (Python 3.11+)
pip install -r backend/requirements.txt

# 2. Start backend + UI
./scripts/start.sh                # http://localhost:8000

# 3. Run a backtest from the CLI
./scripts/run_backtest.sh ensemble

# 4. Run the full strategy tournament
./scripts/run_tournament.sh

# 5. Generate the governance / council artifacts
./scripts/validate.sh

# 6. Run the test suite
PYTHONPATH=. pytest tests/
```

Or with Docker:

```bash
cp .env.example .env
docker compose -f docker/docker-compose.yml up --build
```

## Strategies shipped

| Name           | Family          | Idea                                                                          |
| -------------- | --------------- | ----------------------------------------------------------------------------- |
| `ema_trend`    | trend           | Long when fast EMA > slow EMA and slope is up; ATR-sized stops / take-profits |
| `rsi_meanrev`  | mean reversion  | Counter-trend RSI extremes, gated by trend distance                           |
| `breakout`     | breakout        | Donchian breakout after Bollinger compression                                  |
| `vol_regime`   | regime          | Long only when realized vol is in a healthy band                              |
| `ensemble`     | ensemble        | Confidence-weighted vote across all members; adaptive Sharpe weights          |

## Live trading safety

Live trading is locked by FOUR independent gates. ALL must be true for any live
order to be considered:

1. `configs/live.yaml: locked: false`
2. `SNIPER_EXCHANGE_API_KEY` + `SNIPER_EXCHANGE_API_SECRET` set in environment
3. Governance live-readiness score ≥ 80
4. Explicit human unlock via `POST /api/live/unlock` with confirm string
   `I_ACCEPT_LIVE_RISK`

Even when all four pass, **this build ships without any code that places live
orders**. `LiveGate.is_unlocked()` is the chokepoint a future executor would
have to call through — it is the only place to flip from research/paper to
live execution.

See [docs/LIVE_SAFETY_RULES.md](docs/LIVE_SAFETY_RULES.md).

## Documentation

- [QUICKSTART](docs/QUICKSTART.md)
- [ARCHITECTURE](docs/ARCHITECTURE.md)
- [API_DOCS](docs/API_DOCS.md)
- [LIVE_SAFETY_RULES](docs/LIVE_SAFETY_RULES.md)
- [NEXT_STEPS](docs/NEXT_STEPS.md)
