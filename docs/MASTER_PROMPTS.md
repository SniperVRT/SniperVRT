# SniperVRT — 5 Master Prompts (Audited 2026-06-12)

Five maximum-coverage prompts that absorb every gap from the council review plus an additional audit pass. Each prompt is **self-contained** — paste into a fresh Claude Code session with no prior context required. Each maps to a complete vertical of work.

## Project state snapshot (paste at top of every prompt)

```
REPO: github.com/SniperVRT/SniperVRT, branch claude/kalshi-mispricing-engine-VDGf6
DEADLINE: June 22, 2026, 11:59 PM (PROFIT REQUIRED)
PWD: /home/user/SniperVRT
PYTHON: 3.11, install with `pip install -e kalshi/[dev]` and `pip install -e copy_trade/[dev]`

TWO SUBSYSTEMS:
  kalshi/        Probability mispricing engine for Kalshi event markets
                 93 tests passing. RSA-signed API, 3 strategies, ensemble,
                 9-lock governance, 7-scenario rehearsal, paper executor.
                 LIVE PATH: live.py:217 `log.info("live_submit_dispatched")`
                 is a STUB — does NOT actually POST to /portfolio/orders.

  copy_trade/    Hyperliquid vault meta-allocator (US-legal perp DEX).
                 47 tests passing. Bitget→Hyperliquid pivot done.
                 Vault = copy-trade primitive (deposit=subscribe).
                 Uses official hyperliquid-python-sdk for EIP-712 signing.
                 24h lockup tracked. Paper + backtest engines built.
                 Mode column on subscriptions isolates live/paper/backtest.
                 LIVE PATH: vault_usd_transfer NEVER exercised against testnet.

CORE DOCS:
  docs/HANDOFF_AND_COUNCIL_REVIEW.md     Full project history
  docs/COUNCIL_REVIEW_2026_06_12.md      Earlier 15-prompt council review
  docs/MASTER_PROMPTS.md                 THIS FILE — 5 consolidated prompts

CONSTRAINTS:
  - Be EXTREMELY token-conservative. No premature abstractions.
  - Additive edits to working modules; do not refactor for refactor's sake.
  - Every new module must have tests. Maintain 100% green test suite.
  - Never write LIVE-execution code that runs without an explicit flag.
  - DEFAULT to dry_run=True everywhere money can move.
  - SQLite with PRAGMA foreign_keys=ON, WAL mode. Always insert parent
    rows (markets, masters) before children (snapshots, signals).
  - All times UTC ISO-8601. All money values floats USD.
  - Use the official hyperliquid-python-sdk; do NOT reimplement EIP-712.
```

---

## PROMPT 1 — Live Execution Readiness

> **Goal:** Both subsystems become capable of safe, audited, real-money execution. After this prompt, `kalshi-engine scan` and `copy-trade rebalance --live` can move real capital if and only if every safety gate is green.
>
> **Build / modify:**
>
> 1. `kalshi/kalshi_engine/connectors/kalshi.py` — add `signed_post(path, body)` method that builds the v2 RSA-PSS signature over `timestamp + "POST" + path + canonical_json(body)`, posts to Kalshi, retries on 429/5xx with exponential backoff (mirror `_get`). Add idempotency: pass-through `client_order_id` in body, treat HTTP 409 as "already submitted, treat as success".
> 2. `kalshi/kalshi_engine/execution/live.py:submit` — replace the stub `log.info("live_submit_dispatched")` (line ~217) with a real `client.signed_post("/portfolio/orders", payload)` call. Parse `order_id` from response, persist into `live_orders.exchange_order_id` (add this column via additive migration). On any HTTP error, engage `LOCK_REPEAT_FAILURE` and mark `live_orders.status='error'` with truncated error string. Add 30s reconciliation poll job that compares local order state to `/portfolio/orders/{id}` and applies fills via existing `reconcile_order`.
> 3. `kalshi/kalshi_engine/worker.py` (new) — APScheduler-based continuous worker: scan every 5 min, health-check every 15 min, daily report at 00:00 UTC, drift summary every hour, reconciliation poll every 30s while any order is in `submitted` or `partial` state. Trap SIGTERM/SIGINT, drain in-flight orders before exit (max 30s).
> 4. `kalshi/kalshi_engine/connectors/kalshi_ws.py` (new) — WebSocket subscription to `orderbook_delta` and `trade` channels for any ticker we hold an open signal/position on. On each delta, re-evaluate the signal's edge; if edge collapses below `min_edge / 2`, write an `edge_decay` exit advisory into `signals` (status='expired_edge_decay'). No auto-execution from WS, only advisory.
> 5. `copy_trade/validation/testnet_roundtrip.py` (new) — script + CLI `copy-trade testnet-roundtrip`: requires `HYPERLIQUID_TESTNET=true` and a testnet wallet with ≥10 USDC. Steps: (a) pick smallest open vault from `vaultSummaries`, (b) `follow(vault, allocated_usdt=5)`, (c) poll `user_vault_equities` for up to 60s until our entry appears, (d) attempt `unfollow` immediately and assert it fails or vault returns lockup error, (e) record the lockup_until timestamp, (f) emit a `testnet_roundtrip` row to a new `validation_runs` table with `passed_at` timestamp on success. Block `copy-trade rebalance --live` and `copy-trade worker --live` in CLI unless `validation_runs` has a row with `passed_at` within the last 30 days.
> 6. `copy_trade/security/keystore.py` (new) — Fernet-encrypted private key store. CLI `copy-trade keystore-init` prompts for the wallet private key and a passphrase, writes encrypted blob to `data/keystore.bin`. CLI `copy-trade keystore-rotate` re-encrypts with a new passphrase. `HyperliquidConnector.__init__` reads `KEYSTORE_PASSPHRASE` env var (for daemon mode) or prompts via getpass (interactive mode); the decrypted key lives only in memory. Remove `hyperliquid_private_key` from `CopyTradeSettings` once keystore is wired. Refuse worker startup if keystore missing in live mode. Critically: ensure the decrypted key NEVER appears in any structlog event, exception traceback, or repr. Add a unit test that runs `pytest -s` capturing stderr and grepping for the key.
> 7. `copy_trade/validation/promotion.py` (new) — `PromotionReport` dataclass and `check_ready_for_live(conn, settings) -> PromotionReport`. Asserts ALL of: (a) ≥7 calendar days of `trader_snapshots` history, (b) ≥3 successful paper-rebalance runs in last 7d (track in a new `paper_rebalance_runs` table), (c) Spearman rank correlation between `trader_scores.composite_score` and realised `subscription_pnl.total_pnl` ≥ 0.20 over last 14d (use existing `validation/tracker.py`), (d) `validation_runs` row with `passed_at` < 30d old, (e) `settings.live_enabled=True` and copy_trade equivalent of `live_dry_run=False`, (f) no unresolved `safety_events`, (g) `emergency_force_unwind=True`. Wire into CLI: `copy-trade rebalance --live` and `worker --live` call this first; if blockers non-empty, print them all and exit 1. Mirror logic for Kalshi by extending the existing `scripts/readiness_report.py` to require the same kind of paper-trade-history check.
> 8. `copy_trade/risk/watchdog.py` (new) — separate APScheduler job (5-min period) that for every active live subscription: pulls current vault equity, compares to entry equity stored in `subscriptions.entry_equity_usdt` (add this column), if drop ≥ `settings.per_vault_emergency_drawdown_pct` (default 0.20) emits an emergency `AllocationDecision(action='unsubscribe')` for THAT vault, persists a `safety_events` row of type `vault_blowup`, sends a notification (see Prompt 4 for notifier — if Prompt 4 isn't done yet, log loudly at WARNING). Respects lockup. Wire into both `worker.py` and as standalone CLI `copy-trade watchdog --once`.
> 9. Add data-quality gates everywhere data crosses a boundary: `connectors/kalshi.py:_parse_market` rejects markets with negative volume or close_time in the past or > 2 years out; `platforms/hyperliquid.py:_parse_vault_summary` rejects vaults with negative TVL or `createTimeMillis` in the future. Failed parses go into a new `data_quality_failures` table with the raw payload for audit.
> 10. Add a circuit breaker: any connector that experiences ≥5 consecutive HTTP errors trips a 5-minute cool-down where it returns cached/empty responses. Implement as a small `connectors/_circuit.py` shared utility.
>
> **Tests:** every new module has tests. Specifically: signed-POST signature determinism, idempotency on 409, testnet-roundtrip CLI runs with mocked Hyperliquid, promotion gates each pass/fail independently, watchdog triggers + respects lockup, keystore round-trips + key never leaks. Maintain 140 → 200+ passing tests.
>
> **Acceptance:** 
> - `kalshi-engine scan` against a real Kalshi key WITHOUT `LIVE_ENABLED` is unchanged.
> - `copy-trade rebalance --live` exits 1 with a labelled blocker list on a fresh DB.
> - `copy-trade testnet-roundtrip` completes cleanly against testnet.
> - `copy-trade keystore-init` produces an encrypted file; `cat data/keystore.bin` returns binary garbage; running the worker with the wrong passphrase fails closed.
> - All existing 140 tests still pass; new tests bring suite to ≥ 200.
>
> **Stay token-conservative:** reuse existing patterns, don't introduce new frameworks. Use stdlib (`getpass`, `cryptography.fernet`, `apscheduler`) only. No new third-party deps.

---

## PROMPT 2 — Risk Management + Unified Portfolio

> **Goal:** Treat SniperVRT as a single hedge-fund portfolio across Kalshi + copy-trade. Risk budget is shared, correlations are observed at the portfolio level, sizing uses fractional Kelly or risk parity, daily VaR is computed, and one breach in either subsystem halts both.
>
> **Build / modify:**
>
> 1. `unified/` (new top-level package) — `unified/__init__.py`, `unified/db.py` opening a third SQLite DB at `data/unified.db` with schema (`unified/schema.sql`): `unified_snapshots(captured_at, kalshi_equity, copytrade_equity, total_equity, kalshi_deployed, copytrade_deployed, total_drawdown_pct, combined_locks_json, active_subscriptions_n, open_signals_n)`. `unified/aggregator.py:aggregate(now)` reads from both `kalshi/data/kalshi.db` and `copy_trade/data/copy_trade.db` (path via env vars `KALSHI_DB_PATH`, `COPYTRADE_DB_PATH`), computes the snapshot, writes to `unified.db`.
> 2. `unified/safety.py` — `cross_system_safety_check(unified_conn, settings)`. If `unified_snapshots.total_drawdown_pct >= settings.unified_max_drawdown_pct` (default 0.15), write a `unified_safety_events` row of type `cross_system_drawdown_stop`, and engage `LOCK_EMERGENCY` in BOTH kalshi.db's `governance_locks` table AND copy_trade.db's `safety_events` (with type `drawdown_stop`). Both subsystems already check their own locks on every action, so this cascades the halt. Wire as APScheduler job running every 5 minutes in a new `unified/worker.py`.
> 3. `copy_trade/risk/portfolio_risk.py` (new) — `compute_correlation_matrix(conn)` returns NxN Pearson on equity-return series from `trader_snapshots.raw_json -> portfolio.allTime.accountValueHistory` for the currently ACTIVELY-HELD vaults (mode='live'). `compute_avg_pairwise_corr(matrix)` returns mean off-diagonal. `same_leader_concentration(conn)` joins `subscriptions` with `masters` via vault details `leader` field (extract from snapshot.raw_json) and returns dict `{leader: total_allocated_usdt}`. Persist results to `portfolio_risk_snapshots`. Engage new `LOCK_CONCENTRATION` if avg_corr > 0.5 OR any single leader > 30% of capital.
> 4. `copy_trade/risk/var.py` (new) — historical 1-day VaR at 95% and CVaR at 95%, computed from the last 30 days of `portfolio_snapshots` equity-curve daily returns. If we have <30 days of data, fall back to per-vault Monte Carlo using bootstrap of vault return histories. Persist into a new `risk_metrics` table (daily snapshot). Wire CLI `copy-trade risk-snapshot` printing VaR, CVaR, avg_corr, top-3 leader concentrations.
> 5. `copy_trade/risk/stress.py` (new) — three deterministic scenarios: (a) **crypto_crash**: every active vault loses 30% in 24h, compute portfolio drawdown; (b) **single_blowup**: largest holding loses 50%; (c) **correlation_unification**: all vaults' daily returns realised as the worst single vault's. Output a table of (scenario, predicted_loss_usdt, predicted_drawdown_pct, would_breach_emergency_gate). CLI `copy-trade stress-test`.
> 6. `copy_trade/allocation/sizing.py` (new) — alternative allocation methods, selectable via `settings.allocation_method ∈ {score_weighted (default), fractional_kelly, risk_parity}`. (a) **fractional_kelly**: per vault i, `mu_i = mean(rolling_30d_returns)`, `sigma_i = std(rolling_30d_returns)`, `kelly_i = max(0, mu_i / max(sigma_i**2, 1e-6))`, scaled by `settings.kelly_fraction` (default 0.25); then clip to `[min_pct, max_pct]` and normalise. (b) **risk_parity**: weight inversely to vault vol so each contributes equally to portfolio variance — `w_i = (1/sigma_i) / sum(1/sigma_j)`. Both methods skip vaults with `sigma_i == 0` or `mu_i <= 0`. Wire into `allocation/portfolio.py:compute_allocations` via the new setting. Add CLI `copy-trade allocation-method --set fractional_kelly`.
> 7. Add **liquidity-adjusted exposure metric** to `risk/portfolio_risk.py`: for each active sub, `liquid_exposure = allocated_usdt × (1 if lockup_passed else 0)`. Report locked vs unlocked portion in CLI `copy-trade status`. Refuse new deposits if `locked_pct > settings.max_locked_pct` (default 0.80, meaning we always keep ≥20% withdrawable).
> 8. Mirror in Kalshi: `kalshi/kalshi_engine/risk/portfolio_risk.py` (new) — for open paper/live positions, compute per-category exposure correlation (do simultaneous "Will inflation be > 3% in May" and "Will Fed cut rates" markets share a common risk factor? — use a simple lookup table of category→risk_factor in `risk/categories.py` and engage `LOCK_CONCENTRATION` if any single risk_factor > settings.max_factor_exposure_usd).
> 9. `unified/dashboard.py` (new) — Streamlit page rendering combined Kalshi + copy-trade view: total equity curve (sum of both), per-system PnL breakdown, all active locks across both systems, risk metrics from prompt 4 (VaR/CVaR/avg_corr), stress test results, and a big red banner if any `unified_safety_events` is unresolved. Launch via `streamlit run unified/dashboard.py`.
> 10. CLI consolidation: add a top-level `snipervrt` script (entry point in a new root `pyproject.toml`) that dispatches to either `kalshi-engine` or `copy-trade` subcommands plus new `snipervrt unified` group (snapshot, dashboard, safety-check, stress, risk).
>
> **Tests:** correlation matrix math, VaR computation on synthetic returns, stress-scenario predictions, fractional Kelly edge cases (zero sigma, negative mu), risk-parity normalisation, unified safety cascade engages locks in both DBs, liquidity-adjusted exposure cap. Maintain green suite, target ≥ 240 tests.
>
> **Acceptance:**
> - `snipervrt unified snapshot` writes a snapshot reading both subsystem DBs.
> - Manually engaging a lock in one subsystem reflects in the unified dashboard within 5 minutes.
> - `copy-trade stress-test` outputs realistic numbers for the three scenarios.
> - Changing `allocation_method` to `fractional_kelly` and re-running `paper-rebalance` produces noticeably different allocations than score-weighted (more concentrated).
> - `snipervrt unified safety-check` engages emergency locks in BOTH DBs when given a synthetic 16% drawdown snapshot.

---

## PROMPT 3 — Statistical Rigor + Validation

> **Goal:** Prove the edge is real before scaling capital. Walk-forward validation, bootstrap confidence intervals, parameter sweeps over robust regions, statistical baselines as null hypotheses, isotonic calibration on Kalshi, regime-conditional weights on copy-trade. After this prompt, we have defensible numbers — not point estimates — for both subsystems.
>
> **Build / modify:**
>
> 1. `copy_trade/backtest/walk_forward.py` (new) — extends `engine.py` with rolling out-of-sample testing. Parameters: `train_days` (default 14), `test_days` (default 1), `step_days` (default 1). Loop: for each window start T, train weights on `[T, T+train_days)`, evaluate on `[T+train_days, T+train_days+test_days)`, record OOS Sharpe. Final reported Sharpe is the mean of OOS windows, NOT in-sample. Persist to extended `backtest_runs` table with new columns `oos_sharpe, oos_return, n_windows`.
> 2. `copy_trade/backtest/bootstrap.py` (new) — `bootstrap_ci(returns: list[float], n_resamples=1000, ci=0.95)` returns `{mean, lo, hi}` for the metric over bootstrap resamples. Apply to final equity curve from a walk-forward run; report 95% CI on Sharpe, total return, max drawdown. Add to `backtest_runs` columns: `sharpe_ci_lo, sharpe_ci_hi, return_ci_lo, return_ci_hi`.
> 3. `copy_trade/backtest/sweep.py` (new) — `run_sweep(param_name, value_range, settings)` — for each value, run a full walk-forward backtest, collect (param, oos_sharpe, ci_lo, ci_hi). Output the "robust region" = the contiguous param range where ci_lo > 0 (we're confident the strategy is positive). CLI `copy-trade backtest-sweep --param weight_sharpe --range 0.2,0.6,0.05`.
> 4. `copy_trade/validation/baseline.py` (new) — three null-hypothesis strategies tested on the same snapshot history through the same backtest engine: (a) `equal_weight_top_n(n=10)` picks top 10 by raw APR equal-weight, (b) `random_5(seed=42)` picks 5 random eligible vaults, (c) `always_top_1` picks the single highest-APR vault. CLI `copy-trade baselines` reports our score-weighted Sharpe vs each baseline. PROMOTION GATE: refuse `--live` unless our walk-forward OOS Sharpe ≥ `equal_weight_top_n + 0.5`. If we can't beat equal-weight, our scoring isn't earning its complexity.
> 5. `copy_trade/validation/calibration.py` (new) — calibration plot for paper PnL: bucket realised PnL by predicted score percentile (10 buckets), plot empirical PnL per bucket. Persist data to `score_calibration` table. CLI `copy-trade calibration-plot` prints text histogram. Compute and persist a calibration error metric (mean abs deviation across buckets); fail PROMOTION GATE if calibration error > 0.30.
> 6. `kalshi/kalshi_engine/validation/isotonic.py` (new) — isotonic regression calibrator over resolved markets. Inputs from `signals` joined with `resolutions`. Fit `sklearn.isotonic.IsotonicRegression` on (model fair_prob → realised outcome 0/1). Save model to `data/isotonic_kalshi.pkl`. At signal-build time (`core/signals.py:build_signal`), apply calibrator if model exists: `calibrated_fair = calibrator.predict(raw_fair)`. Add CLI `kalshi-engine recalibrate` to refit. (Use scipy `isotonic_regression` if sklearn footprint is undesired.)
> 7. `kalshi/kalshi_engine/validation/baseline_kalshi.py` (new) — null hypothesis: always-trade-when-implied-prob-30-70%. Backtest against resolved markets. Our ensemble's Brier score must beat this baseline by ≥ 0.02 before live trading. Persist to `model_performance` table.
> 8. `copy_trade/regime/classifier.py` (new) — HMM (use `hmmlearn`, add to deps) on a feature vector of `[BTC realised vol 24h, BTC funding rate, BTC OI change]` pulled from Hyperliquid public endpoints. Three-state model: trending / ranging / high_vol. Train on ≥ 90 days when available; fall back to a simple rule-based classifier (vol < 30% annualised AND |funding| < 0.01 → ranging; vol > 80% → high_vol; else → trending). Tag every scoring run with current regime in `trader_scores.regime`. `ranking/score.py:score_traders` accepts an optional `regime` arg and uses regime-conditional preset weights: trending = boost roi_7d & sharpe, ranging = boost consistency & calmar, high_vol = boost mdd weight (penalty for drawdown).
> 9. `copy_trade/validation/lookhead_audit.py` (new) — automated audit that runs after every backtest: confirms no `trader_snapshots` row with `captured_at > tick_ts` was used in any tick's scoring. Asserts via DB query that for each persisted backtest_run, the snapshot IDs referenced in `trader_scores` for that run all have `captured_at <= scored_at`. Run as a pytest fixture in `tests/test_lookahead.py`.
> 10. `copy_trade/validation/sample_size.py` (new) — guard helpers: `require_min_samples(n, min_n, metric_name)` raises clear error if undersized. Wire into Sharpe / Calmar / VaR computations — refuse to report a Sharpe if N < 20 returns; flag with `(n=X, low_confidence)` annotation if 20 ≤ N < 50.
> 11. Add **model versioning**: every persisted backtest_run, trader_score, and signal records a `model_version` string (e.g., "v1.2-fractional-kelly-30d"). New `model_versions` table with `(version, params_json, started_at, ended_at, notes)`. CLI `copy-trade model-versions` lists. Lets us compare historical performance across weight changes.
> 12. `copy_trade/validation/ab_test.py` (new) — simple A/B framework: split capital 50/50 between two `model_version` configs running in parallel paper mode. After N days, compare OOS Sharpe with bootstrap CIs. CLI `copy-trade ab-start --variant-a baseline --variant-b new` and `ab-status`. Promotes variant whose ci_lo > other's ci_hi.
>
> **Tests:** walk-forward window math, bootstrap CI on a known synthetic distribution, sweep robust-region detection, baseline backtest comparisons, isotonic calibration improves Brier on a synthetic miscalibrated input, regime classifier produces all three states given diverse input, look-ahead audit catches a deliberately-introduced violation, sample-size guard raises at thresholds. Target ≥ 290 tests.
>
> **Acceptance:**
> - `copy-trade backtest --walk-forward` outputs OOS Sharpe with 95% CI.
> - `copy-trade baselines` shows our scoring ranked against three nulls.
> - `copy-trade backtest-sweep --param weight_sharpe` identifies a robust contiguous region.
> - `kalshi-engine recalibrate` reduces Brier on a held-out set.
> - `copy-trade ab-start` creates parallel paper allocations; `ab-status` reports CI overlap.

---

## PROMPT 4 — Observability + Reliability Operations

> **Goal:** Know everything that happens; recover from anything. Persistent event log, raw payload retention, notifications on critical events, healthcheck endpoints, deployment artefacts, graceful shutdown, backups, log rotation, metrics. After this prompt, leaving the system unattended for 7 days is safe.
>
> **Build / modify:**
>
> 1. `copy_trade/notifications/__init__.py`, `copy_trade/notifications/alerts.py` (new) — pluggable `Notifier` ABC. Concrete implementations: `SlackNotifier(webhook_url)`, `DiscordNotifier(webhook_url)`, `EmailNotifier(smtp_host, port, user, password, from, to)`. `CompositeNotifier([list_of_notifiers])` fans out. Each notifier method takes a `NotificationEvent(severity, title, body, context_dict)` dataclass. Severities: info, warn, critical. Failure to deliver one notifier never blocks others. Settings: `slack_webhook_url`, `discord_webhook_url`, `notification_email_*`. Default: stdout notifier (no external dep needed for tests).
> 2. Trigger hooks: wire `Notifier` into safety_events writes, lock engagements, watchdog triggers, daily summary at 00:00 UTC, weekly performance report at Sunday 00:00 UTC, promotion-gate failures. Mirror to Kalshi via parallel `kalshi/kalshi_engine/notifications/` with the same Notifier ABC. Add idempotency: a `sent_notifications` table tracks `(event_hash, sent_at)` so we don't double-notify on retry.
> 3. Persistent event log: extend both `db.py`s with `event_log(id, ts, level, logger, event, payload_json, host, model_version)`. Add a structlog processor that writes every event to this table (in addition to stdout). Add CLI `copy-trade events --tail 100`, `--since 1h`, `--level warning+`. Mirror in Kalshi. The structlog processor must not raise even if DB is closed — wrap in try/except → fall back to stderr.
> 4. Raw payload retention: new `raw_payloads(id, ts, source, request_hash, payload_json)` table. `KalshiClient._get` and `HyperliquidConnector._post_info`/`enrich_with_details` write each response into this table BEFORE parsing. Add `data_retention_days` setting (default 90). Background job `prune_old_payloads` runs daily, deletes payloads older than retention. Enables replay-from-raw-payload for debugging "why did we make this decision on 2026-05-15".
> 5. HTTP healthcheck endpoints: new `copy_trade/ops/healthserver.py` runs an aiohttp (or stdlib `http.server`) on port 8081 returning JSON `{status: ok|degraded|down, checks: {db, connector, watchdog, scheduler}, timestamp}`. Kalshi mirror on port 8080. Workers start the healthserver in a background thread. `GET /metrics` returns Prometheus text format for: deployed_capital, open_signals, active_subscriptions, active_locks_n, drawdown_pct, last_scan_age_seconds.
> 6. Graceful shutdown: both worker.py modules trap SIGTERM/SIGINT, set a shutdown_flag the scheduler checks each tick, finish in-flight DB transactions, close connections cleanly, wait max 30s, then SIGKILL self. Add `tests/test_shutdown.py` using subprocess + signal.
> 7. SQLite WAL checkpoint job: APScheduler nightly job runs `PRAGMA wal_checkpoint(TRUNCATE)` on both DBs to prevent WAL file growth. Backup job: every 6h runs `sqlite3 .backup data/backups/{db_name}_{timestamp}.db` atomically and keeps last 14 days; `infra/backup.sh` does the same from cron as fallback. Restore procedure documented in `docs/RUNBOOK.md`.
> 8. Log rotation: structlog file output rotated nightly with `logging.handlers.TimedRotatingFileHandler` to `logs/copy_trade.log.YYYY-MM-DD`, keep 30 days. Same for Kalshi.
> 9. Deployment artefacts: `Dockerfile` per subsystem (python:3.11-slim, multi-stage with pip wheel cache, non-root user, EXPOSE 8080/8081, ENTRYPOINT runs the worker). `infra/docker-compose.yml` orchestrating kalshi-worker, copy-trade-worker, unified-worker, both dashboards (Streamlit) on 8501/8502, a shared `data/` volume. `infra/systemd/snipervrt-kalshi.service`, `snipervrt-copytrade.service`, `snipervrt-unified.service` for systemd hosts. `.dockerignore` excludes data/, logs/, __pycache__, .env, secrets/, keystore.bin.
> 10. `docs/RUNBOOK.md` (new) — incident response: "API rate limit hit" → "vault drawdown >20%" → "Hyperliquid 502" → "passphrase forgotten" → "DB corruption". Each with concrete commands. `docs/ARCHITECTURE.md` (new) — diagram (mermaid) of: ingest → score → allocate → execute → reconcile → report; plus the cross-system safety cascade.
> 11. `copy_trade/dashboard.py` (new — sibling to Kalshi's) — Streamlit with persistent status strip, masters table, scores table, current allocations, PnL chart, safety events. Auto-refresh every 30s via `st.experimental_rerun()` after a sleep.
> 12. Connection-pool safety: replace single-connection-per-thread pattern with a small `db.py:get_pool()` returning a connection from a queue of N=5 connections, all with `check_same_thread=False`. Wrap every connection usage in `with pool.acquire() as conn:`. Prevents lockups on concurrent writes from scheduler + healthcheck + watchdog.
>
> **Tests:** notifier fan-out + idempotency, event-log writes don't crash on closed DB, payload retention prunes correctly, healthcheck returns correct JSON for healthy + degraded states, /metrics returns valid Prometheus text, graceful shutdown completes within 30s, WAL checkpoint reduces file size, backup-restore round-trip on a synthetic DB. Target ≥ 340 tests.
>
> **Acceptance:**
> - `docker-compose up` brings up both workers + both dashboards + unified worker.
> - `curl http://localhost:8081/health` returns `{"status":"ok",...}`.
> - `curl http://localhost:8081/metrics` returns Prometheus format.
> - Triggering a safety event sends a Slack message (if webhook configured).
> - SIGTERM to worker exits cleanly within 30s.
> - `copy-trade events --tail 20` lists recent log lines from DB.
> - Backup CRON produces a valid SQLite file every 6h; restoring it on a fresh box yields a working system.

---

## PROMPT 5 — External Edge + Compliance + Documentation

> **Goal:** Expand alpha sources beyond on-platform data; meet US compliance obligations; produce permanent documentation. After this prompt, the system can be handed off, audited, taxed, and grown.
>
> **Build / modify:**
>
> 1. `copy_trade/external/twitter.py` (new) — Twitter/X sentiment scraper using `twscrape` (no API key). Configurable keyword/account list per vault leader (some leaders post their thesis on X). Persist tweets to a new `external_tweets` table. Compute simple bullish/bearish score using a tiny rule set (counts of "long", "buy", "bearish", "short" in tweets in last 24h) — no LLM. Wire into scoring as a small (weight 0.05) bonus/penalty in `trader_scores` via `evidence_bonus` column. Mirror for Kalshi: tag markets to topic-relevant accounts (e.g., Fed-related markets → Fed officials) and use the same bullish/bearish scoring as an additional `evidence` field.
> 2. `copy_trade/external/onchain.py` (new) — pull from Hyperliquid public endpoints: funding rates per coin (`/info {"type":"metaAndAssetCtxs"}`), open interest, top wallets' position changes. Compute aggregate "crypto risk-on / risk-off" score: positive funding + rising OI → risk-on. Tag into `regime/classifier.py` features.
> 3. `copy_trade/external/macro_calendar.py` (new) — embed a hardcoded calendar of major US events for the next 90 days (FOMC, NFP, CPI, PCE — copy from public Fed schedule). Within ±2h of any event, the worker enters "caution mode" reducing `settings.total_capital_usdt` effective for sizing by 50% (no new deposits, allow existing). Persist active-caution windows.
> 4. `copy_trade/compliance/tax_lots.py` (new) — FIFO tax-lot ledger. Every deposit creates a `tax_lots(id, vault, opened_at, units_or_usdt, cost_basis_usdt)` row. Every withdrawal closes oldest open lots first, computes `realised_gain = proceeds - cost_basis`, `holding_period_days`. Distinguish short-term vs long-term (≤365d / >365d). CLI `copy-trade tax-report --year 2026 --format csv` outputs IRS Form 8949 compatible CSV: `description, acquired_date, sold_date, proceeds, basis, gain_loss, term`.
> 5. `docs/COMPLIANCE.md` (new) — clear US-user disclaimer: not investment advice, no fiduciary duty, user responsible for taxes on every entry/exit, system tested on US-resident user only, Hyperliquid is non-custodial — user holds keys, user holds risk. Include link to Form 8949 IRS instructions.
> 6. `docs/ARCHITECTURE.md` — diagrammed end-to-end (mermaid sequence + component diagrams): ingest → score → allocate → execute → reconcile → report. Show both subsystems and the unified layer. Document every SQLite table and its FK relationships. Document every CLI command.
> 7. `docs/RUNBOOK.md` — operational procedures (overlap with Prompt 4 but expand): cold-start checklist, daily checks, weekly checks, monthly recalibration, quarterly key rotation, incident response per failure mode, backup/restore drill, disaster recovery.
> 8. `docs/SETUP.md` (new) — fresh-host deployment from zero: install python, clone, pip install, `kalshi-engine init-db`, `copy-trade init-db`, `copy-trade keystore-init`, `copy-trade poll-leaderboard`, `copy-trade testnet-roundtrip`, `copy-trade paper-rebalance`, monitor, promote.
> 9. `docs/DECISIONS.md` (new) — decision log: why Hyperliquid (research-backed), why Sharpe + Calmar weights (research + audit), why 24h lockup tracking (Hyperliquid rule), why 30% per-master cap (research), why 0.70 correlation threshold. Each entry: date, decision, alternatives considered, outcome.
> 10. CI/CD: `.github/workflows/test.yml` runs both subsystems' pytest + ruff + mypy strict on every push. `.github/workflows/release.yml` on tag push builds Docker images. `pyproject.toml` adds `[tool.ruff]` strict config and `[tool.mypy]` strict mode (typed throughout).
> 11. Pre-commit hooks: `.pre-commit-config.yaml` running ruff format + lint + mypy on staged Python files. README.md updated with badges (test status, coverage, ruff).
> 12. Per-CLI command help: every typer command has a complete docstring covering "what it does, when to run it, what it produces, what it costs in API calls / time / capital, what to check after". Generate `docs/CLI.md` from `--help` output.
> 13. Investor-style report: `copy-trade investor-report --month 2026-06` produces a one-page PDF (use `reportlab`) with equity curve, top 5 vaults by PnL contribution, drawdown chart, allocations pie, key metrics. Mirror for Kalshi. Mirror for unified. Mail via the email notifier on monthly schedule.
>
> **Tests:** Twitter sentiment scoring on canned inputs, on-chain feature computation, macro-calendar caution-mode engagement, tax-lot FIFO math + 8949 CSV format, doc generation produces valid markdown, CI workflow YAML lints. Target ≥ 380 tests.
>
> **Acceptance:**
> - `copy-trade external-poll` populates `external_tweets` from real X data.
> - `copy-trade tax-report --year 2026 --format csv > taxes.csv` opens cleanly in Excel and matches reconciled total realised PnL.
> - `.github/workflows/test.yml` passes on a fresh push.
> - `docs/ARCHITECTURE.md` renders Mermaid diagrams correctly in GitHub.
> - Monthly cron emits the investor-report PDF.
> - A new developer following `docs/SETUP.md` from scratch on a clean Ubuntu host can run `copy-trade testnet-roundtrip` successfully.

---

## Execution order

Given the **June 22 deadline (11 days)**, run prompts in this order:

| Day | Prompt | Why now |
|---|---|---|
| Today | Start `copy-trade poll-leaderboard` daily (no key needed) | Build snapshot history NOW so Prompt 3 has data |
| Day 1-2 | **Prompt 1** | Blocks all live execution; nothing else matters until done |
| Day 3-4 | **Prompt 4** (parallel) | Need to see things break before $200 is on the line |
| Day 4-5 | **Prompt 2** | Risk hardening before mainnet |
| Day 6-7 | **Prompt 3** | Validation; may invalidate strategy choice — better to know |
| Day 6 | Mainnet first $200 if Prompt 1+4 done | Real-world feedback |
| Day 8-11 | **Prompt 5** + scaling | Compliance + alpha expansion |

## Pre-flight checklist before any `--live`

- [ ] Prompt 1 acceptance criteria all green
- [ ] `copy-trade testnet-roundtrip` ran in last 30 days
- [ ] `copy-trade keystore-init` done; passphrase memorised
- [ ] `copy-trade rebalance --live` outputs "LIVE_READY" (no blockers)
- [ ] Slack/Discord webhook configured (Prompt 4)
- [ ] Watchdog scheduled (Prompt 1 #8)
- [ ] Backup CRON scheduled (Prompt 4)
- [ ] Healthcheck returns ok (Prompt 4)
- [ ] First mainnet deposit ≤ $200, monitor for 24h, then scale
