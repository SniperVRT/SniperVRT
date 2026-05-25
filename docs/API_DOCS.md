# API Reference

All routes are mounted under `/api`. Interactive OpenAPI docs are auto-generated
at http://localhost:8000/docs.

## Health & status
- `GET  /api/health` — liveness probe
- `GET  /api/system/status` — unified status snapshot (paper, live gate, data
  quality, config)

## Market data
- `POST /api/data/refresh?symbol=&timeframe=&exchange=&limit=` — fetch new
  candles (or advance synthetic series)
- `GET  /api/data/candles?limit=500` — OHLCV array
- `GET  /api/data/quality` — candle count, missing, duplicates, coverage
- `GET  /api/data/regime` — bull / bear / chop / high_vol classifier
- `GET  /api/data/price` — last close

## Strategies
- `GET  /api/strategies` — list, including saved configs
- `POST /api/strategies` `{name, strategy_type, params, enabled}` — upsert
- `DELETE /api/strategies/{name}` — remove

## Backtests
- `POST /api/backtest/run` body:
  ```json
  { "strategy_type":"ensemble", "params":{}, "limit":2000,
    "starting_equity":10000, "allow_short":true }
  ```
- `GET  /api/backtest/runs?limit=25` — leaderboard
- `GET  /api/backtest/runs/{id}` — equity curve + trades
- `GET  /api/backtest/runs/{id}/export.json` — write JSON report to disk

## Paper trading
- `POST /api/paper/start` — start the background tick loop
- `POST /api/paper/stop` — graceful stop
- `POST /api/paper/tick` — advance one decision step (sync)
- `POST /api/paper/reset` — wipe account
- `POST /api/paper/active-strategy` `{name}`
- `GET  /api/paper/status`
- `GET  /api/paper/equity-curve?limit=500`
- `GET  /api/paper/positions?open_only=true`
- `GET  /api/paper/trades?limit=200`

## Risk
- `GET  /api/risk/blocks?limit=100` — every rejected trade is recorded
- `GET  /api/risk/limits` — current limit config

## Governance
- `POST /api/governance/validate` — run validation, write all report artifacts
- `GET  /api/governance/readiness` — live-readiness score (0–100) + evidence
- `GET  /api/governance/decisions?limit=25` — decision history

## Learning
- `POST /api/learning/tournament` — run every strategy on the dataset
- `POST /api/learning/sweep` `{strategy, grid:{ param: [values] }}` — coarse
  parameter sweep

## Live trading (gated)
- `GET  /api/live/status` — gate state, readiness score, API key presence
- `POST /api/live/unlock` `{reason, confirm:"I_ACCEPT_LIVE_RISK"}`
- `POST /api/live/relock`
- `POST /api/live/emergency-shutdown` — re-lock + stop paper runtime

## Reports
- `GET  /api/reports` — list files in `reports/`
- `GET  /api/reports/{name}` — read one report

## Events
- `GET  /api/events?limit=100&category=paper` — structured event log

## Settings
- `GET  /api/settings`
- `POST /api/settings/reload` — re-read YAML configs from disk
