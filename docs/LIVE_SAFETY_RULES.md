# Live trading safety rules

## The four-gate lock

Live trading is impossible unless ALL of these are simultaneously true:

| Gate | Source                                        | Default     |
| ---- | --------------------------------------------- | ----------- |
| 1    | `configs/live.yaml: locked: false`            | `locked: true` |
| 2    | `SNIPER_EXCHANGE_API_KEY` + SECRET in env     | empty       |
| 3    | Governance live-readiness score ≥ 80          | typically 20–50 |
| 4    | Human unlock via `POST /api/live/unlock`      | locked      |

`LiveGate.is_unlocked()` composes all four; this returns `True` only when every
gate is satisfied AND the kill switch is off.

## Hard safety primitives

- **Kill switch.** `SNIPER_KILL_SWITCH=true` blocks every order at the risk
  engine, regardless of mode.
- **Emergency shutdown.** `POST /api/live/emergency-shutdown` re-locks the
  gate, stops the paper runtime, and writes an `emergency` event.
- **Risk engine.** Rejections logged to `risk_blocks` with rule name and
  detail. Rules: confidence floor, max position size, max open positions,
  daily loss limit, max drawdown, consecutive-loss cooldown, volatility
  shutdown, data quality, minimum notional, kill switch, account halt.
- **Persistent halt.** When `max_daily_loss` or `max_drawdown` fires, the
  paper account sets `halted=true` with a reason. New trades stay blocked
  until manual reset.

## Secret handling

- API keys are read **only** from environment variables (`SNIPER_EXCHANGE_*`).
- The `.env` file is git-ignored.
- Secrets are never printed to the event log, response bodies, or status
  endpoints. `live/status` only reports `has_api_keys: true|false`.

## Operational expectations before going live

1. Run `./scripts/validate.sh` and check `reports/live_readiness_report.json`.
2. Run paper trading continuously for at least 30 days with ≥ 20 closed trades.
3. Confirm the governance decision is `RECOMMEND_LIVE_PILOT`.
4. Confirm no halts in the last 7 days.
5. Set `configs/live.yaml: locked: false` and start in `sandbox: true` mode
   first.
6. Use `POST /api/live/unlock` with a written rationale every time.

## What this build does NOT do

- It does NOT place live orders. There is no live executor implemented yet.
- It does NOT auto-unlock based on score. Human action is required.
- It does NOT store secrets in the repo or DB.

When you build the live executor, it must:

- Call `LiveGate.is_unlocked()` before every order — fail-closed if `False`.
- Reuse the risk engine and execution simulator's fee/slippage assumptions to
  detect live/paper drift.
- Log every live order to `trades` with `mode="live"` for parity with paper.
