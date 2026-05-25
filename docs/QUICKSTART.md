# Quickstart

## Prerequisites

- Python 3.11+
- (Optional) Docker

## Install

```bash
pip install -r backend/requirements.txt
```

The platform falls back to a deterministic synthetic BTC series when no network
is available, so the rest of this guide works fully offline.

## Start the platform

```bash
./scripts/start.sh
```

Open http://localhost:8000. You should see the Home dashboard with a "PAPER
STOPPED" pill, a "LIVE LOCKED" pill, and the current BTC last price.

## Drive the system from the UI

1. **Market Data tab** → "↻ Refresh dataset"
2. **Strategies tab** → "▶ Run tournament" to backtest every strategy on the
   loaded data and rank them by Sharpe.
3. **Backtest tab** → choose a strategy, click "▶ Run" to inspect equity curve,
   drawdown, win rate, profit factor, and last 25 trades.
4. **Paper tab** → "▶ Start" to run the paper-trading runtime. Use "Tick once"
   to deterministically advance one decision step.
5. **Governance tab** → "▶ Run validation" produces all council artifacts and
   returns a live-readiness score.
6. **Live tab** → see the four gates. Try the unlock workflow — it will not
   actually enable live orders.

## Drive the system from the CLI

```bash
./scripts/run_backtest.sh ensemble       # one strategy
./scripts/run_tournament.sh              # all strategies head-to-head
./scripts/validate.sh                    # produce reports/council_decision.json
```

## Run the test suite

```bash
PYTHONPATH=. pytest tests/
```

## Files generated

- `runtime_state/platform.db` — all persistent state
- `reports/council_decision.json`, `live_readiness_report.json`,
  `risk_report.json`, `paper_summary.json`, `strategy_scoreboard.json`
- `logs/platform.log`

All are safe to delete to reset state.
