# Hedge Fund Council Review — 2026-06-12

Council convened on the SniperVRT project (Kalshi engine + Hyperliquid copy-trade meta-allocator) to identify everything missing before live deployment. Findings grouped by urgency tier, with copy-paste prompts ready for the next Claude session.

---

## Council members

- **CIO / Portfolio Manager** — capital allocation, edge sourcing, benchmarks
- **Head of Quant Research** — statistical rigor, backtesting, factor analysis
- **Head of Risk** — drawdown, VaR, correlation, tail risk
- **Head of Execution** — order management, slippage, latency, fees
- **Head of Data Engineering** — pipelines, persistence, monitoring
- **Head of Operations / DevOps** — deployment, reliability, incidents
- **Head of Technology / Security** — key management, secrets, auth
- **Head of Compliance / Legal** — tax, audit trail, jurisdiction

---

## Tier 1 — MUST have before any real money (do this week)

### A1. Execution validation (Head of Execution)

> Build and run a Hyperliquid testnet round-trip validation harness. Currently `platforms/hyperliquid.py` uses the official SDK's `vault_usd_transfer` but the EIP-712 signing has never been exercised against a live response. Create `copy_trade/validation/testnet_roundtrip.py` that: (1) connects to Hyperliquid testnet using `HYPERLIQUID_TESTNET=true`, (2) deposits a tiny USDC amount into a known test vault, (3) polls until the deposit appears in `user_vault_equities`, (4) attempts withdrawal (expecting lockup rejection on first attempt), (5) waits past lockup and withdraws, (6) asserts the full round-trip succeeds. Add CLI command `copy-trade testnet-roundtrip`. Persist every step into `live_orders`-style audit table. Block the `--live` flag in CLI from running unless a successful round-trip exists in the last 30 days. Acceptance: `copy-trade testnet-roundtrip` completes cleanly on Hyperliquid testnet with a real wallet.

### A2. Kalshi live signed POST + WebSocket worker

> In `kalshi/kalshi_engine/execution/live.py`, the real signed POST to `/portfolio/orders` is currently stubbed (line ~217 has `log.info("live_submit_dispatched", payload=payload)` instead of dispatching). Build the actual signed POST: extend `KalshiClient` with a `signed_post(path, body)` method that mirrors `_get` but signs the timestamp+METHOD+path+body preimage. Wire it into the live adapter's submit path. Then build the continuous worker: create `kalshi/kalshi_engine/worker.py` with APScheduler running scan every 5 minutes, health checks every 15, daily reports at 00:00 UTC, and a Kalshi WebSocket subscription for real-time book updates that triggers re-scoring of any market we hold a signal on. Persist all worker events to a new `worker_events` table. Acceptance: 93 existing tests still pass; new tests cover signed POST + body signing; worker can be started via `python -m kalshi_engine.worker`.

### A3. Pre-live promotion gates

> Build a deterministic "paper-to-live promotion checklist" that blocks `--live` unless every condition is satisfied. Create `copy_trade/validation/promotion.py` with a `PromotionReport` dataclass and a `check_ready_for_live(conn, settings)` function that asserts: (1) ≥ 7 calendar days of trader_snapshots history, (2) ≥ 3 successful paper-rebalance runs in last 7 days, (3) Spearman rank correlation between trader_scores.composite and realised subscription_pnl > 0.2 over collected data (test that scoring actually predicts), (4) ≥ 1 successful testnet round-trip in last 30 days, (5) all of: settings.live_enabled=True, settings.live_dry_run=False (live_dry_run is for Kalshi, copy_trade uses an equivalent `copy_trade_live_dry_run`), (6) no active safety_events, (7) emergency_force_unwind toggle is set. The CLI command `copy-trade rebalance --live` calls this first and refuses unless all green. Acceptance: tests cover each gate independently passing/failing; running against a fresh DB returns LIVE_LOCKED with all 7 blockers listed.

### A4. Encrypted secret storage

> Replace plaintext `HYPERLIQUID_PRIVATE_KEY` env handling. Build `copy_trade/security/keystore.py` that: (1) on first run, encrypts the private key with a user-supplied passphrase using Fernet (cryptography library) and writes to `data/keystore.bin`, (2) on subsequent runs, prompts for the passphrase (or reads from `KEYSTORE_PASSPHRASE` env var for daemon mode) to decrypt, (3) the decrypted key stays only in memory inside `HyperliquidConnector`, never written to disk. Add CLI commands `copy-trade keystore-init` and `copy-trade keystore-rotate`. Refuse to start `worker --live` if keystore is missing. Acceptance: encrypted keystore round-trips; private key never appears in any log line; tests cover wrong-passphrase rejection.

---

## Tier 2 — Should have before real money (parallel work)

### B1. Cross-position correlation matrix + same-leader concentration

> Current `ranking/correlation.py` only does pairwise dedup on candidates. Build portfolio-level correlation monitoring: create `copy_trade/risk/portfolio_risk.py` with `compute_portfolio_correlation_matrix(conn)` that returns an NxN matrix of correlations between ACTIVELY-HELD vaults (not just candidates). Add `same_leader_concentration(conn)` that flags when ≥ 2 active subscriptions share the same leader address (different vaults run by same trader = correlated risk). Persist results to a new `portfolio_risk_snapshots` table. Wire into safety gate: if portfolio avg pairwise correlation > 0.5 OR any same-leader cluster > 30% of capital, engage a new `LOCK_CONCENTRATION` lock that blocks new subscriptions until rebalanced. Add CLI `copy-trade risk-snapshot`. Acceptance: tests cover correlation matrix math, same-leader detection, lock engagement.

### B2. Per-vault drawdown trigger + watchdog

> A single vault crashing should trigger an emergency withdraw of THAT vault, not just portfolio-level alerting. Build `copy_trade/risk/watchdog.py` that polls every 5 minutes (separate APScheduler job) and for each active subscription: computes current vault equity vs entry equity, if drop > settings.per_vault_emergency_drawdown_pct (default 20%), engages `LOCK_VAULT_BLOWUP` and emits an unsubscribe AllocationDecision for that vault (subject to lockup). Wire this into worker.py as a fourth scheduled job. Add notification log line for every trigger. Acceptance: tests cover trigger threshold, lockup-respecting withdraw, lock persistence; manual test with a vault simulated to drop 25% triggers the unwind.

### B3. Notifications and alerting

> Build `copy_trade/notifications/alerts.py` with a pluggable notifier interface supporting Slack webhook, Discord webhook, and email (smtplib). Trigger alerts on: (1) any safety_event written, (2) any governance lock engaged, (3) backtest_run completion, (4) per-vault drawdown trigger, (5) daily portfolio summary at 00:00 UTC, (6) Spearman correlation drop below threshold. Add settings: `slack_webhook_url`, `discord_webhook_url`, `notification_email`. All notifications include: timestamp, event type, portfolio state snapshot, action taken. Acceptance: tests use a fake notifier to confirm hooks fire on correct events; manual test with real webhook posts a formatted message.

### B4. Persistent event log + raw payload retention

> Currently structlog goes to stdout only — post-mortem is impossible. Build `copy_trade/db.py:JSONEventLogger` that wraps structlog and also writes every log line to a new `events` table (id, timestamp, level, logger, event, payload_json, host). Add `kalshi/kalshi_engine/db.py` mirror. For both systems, retain raw API responses: Hyperliquid `vaultDetails` and Kalshi `/markets` raw payloads keyed by request hash for replay/debugging. Add `data_retention_days` setting (default 90). Add CLI `copy-trade events --tail 100` and `--since 1h`. Acceptance: events appear in DB; raw payload table populated; CLI tail works.

---

## Tier 3 — Important but can come right after live launch

### C1. Walk-forward backtest + bootstrap confidence intervals

> Current `backtest/engine.py` does a single forward pass. Quant Research wants: (1) walk-forward optimisation — rolling 7-day training windows, then test on next 1 day, repeat; (2) bootstrap confidence intervals on the final Sharpe/return/MDD numbers (1000 resamples, 95% CI); (3) parameter sweeps over `weight_sharpe`, `weight_calmar`, `min_sharpe` thresholds to find robust regions (not point estimates). Output a sweep heatmap + CI summary. Persist results to extended `backtest_runs` table. Add CLI `copy-trade backtest-sweep --param weight_sharpe --range 0.2,0.6,0.05`. Acceptance: tests cover the walk-forward loop math; sweep CLI runs and produces ranked parameter tables.

### C2. Statistical baseline + calibration plots

> Quant Research: every strategy needs a baseline to beat. Build `copy_trade/validation/baseline.py` implementing two null hypotheses: (a) equal-weight top-10 by raw APR, (b) random-pick 5 eligible vaults. Run the same backtest engine on both. Output relative-to-baseline outperformance — if our Sharpe-weighted approach doesn't beat equal-weight by ≥ 0.5 Sharpe over 30 days of data, scoring weights are wrong. Add calibration plot for paper PnL: 10-bucket histogram of (predicted score percentile) vs (realised PnL percentile), show via CLI `copy-trade calibration-plot`. For Kalshi: similar baseline (always-trade-when-implied-prob-30-70) and an isotonic regression calibrator (`kalshi/kalshi_engine/validation/isotonic.py`) that learns the conditional fair_prob | model_output mapping from resolved markets. Acceptance: baseline backtest runs; calibration data persisted; CLI prints both with Brier scores.

### C3. Cross-system unified portfolio view

> Kalshi and copy-trade currently operate as fully separate processes/DBs. CIO wants ONE risk view. Build `unified/` at repo root with: (1) `aggregator.py` that pulls portfolio state from both `kalshi/data/kalshi.db` and `copy_trade/data/copy_trade.db`, (2) emits a combined daily snapshot to a new `unified_portfolio_snapshots` table in a new `unified/data/unified.db`, (3) a unified safety gate — if total drawdown across BOTH systems > 15%, engage BOTH systems' emergency locks. Build a single Streamlit page at `unified/dashboard.py` showing combined PnL, capital deployed per system, and live status. Acceptance: aggregator pulls correctly from both; safety gate cascades; unified dashboard renders.

### C4. Operations: Docker + systemd + healthcheck

> Build production deployment artefacts: (1) `Dockerfile` per system (kalshi/, copy_trade/) using python:3.11-slim base, multi-stage builds; (2) `infra/docker-compose.yml` orchestrating both workers + dashboards + a shared volume for SQLite DBs; (3) `infra/systemd/` units for direct-host deployment; (4) HTTP healthcheck endpoints on both workers (port 8080 + 8081) returning JSON status from `run_health_checks` for Kalshi and the safety_gate_ok + lock state for copy-trade; (5) graceful shutdown: trap SIGTERM, drain in-flight rebalance, close DB cleanly; (6) `infra/backup.sh` script that uses `sqlite3 .backup` for atomic snapshots. Acceptance: `docker-compose up` brings both systems up; `curl localhost:8080/health` returns 200; SIGTERM cleanly stops worker.

---

## Tier 4 — Strategic enhancements (after first profitable cycle)

### D1. Fractional Kelly sizing + risk parity

> Current `allocation/portfolio.py` is score-proportional. Implement two alternative sizing modes selectable via `settings.allocation_method`: (1) "fractional_kelly" — for each vault i, compute Kelly fraction f_i = μ_i / σ_i² where μ from rolling 30d return, σ from rolling 30d std; multiply by `settings.kelly_fraction` (default 0.25); cap by existing max_allocation_pct. (2) "risk_parity" — weight inversely to vault volatility so every vault contributes equally to portfolio variance. Backtest both against the current score-weighted method using the walk-forward engine; promote whichever wins. Acceptance: tests cover Kelly math edge cases (negative mu, zero sigma); CLI shows comparison plot.

### D2. Regime detection layer

> Build `copy_trade/regime/classifier.py` with a Hidden Markov Model (use `hmmlearn`) on BTC realised volatility + funding rate that classifies market into {trending, ranging, high_vol}. Tag each scoring run with current regime. Train regime-conditional weight presets: in trending regimes, boost momentum-style vaults (high roi_7d); in ranging regimes, prefer mean-reversion-style. Persist regime history. Acceptance: HMM trains on at least 90 days of data; current regime exposed via `copy-trade status`.

### D3. Tax lot tracking + compliance export

> US user has a tax obligation on every vault entry/exit. Build `copy_trade/compliance/tax_lots.py` that maintains a FIFO tax-lot ledger per vault: each deposit creates a lot; each withdrawal closes lots FIFO. Persist to new `tax_lots` table with cost basis, holding period, proceeds. Add CLI `copy-trade tax-report --year 2026` that exports CSV in form 8949 layout. Add disclaimer doc at `docs/COMPLIANCE.md` covering: not investment advice, user responsible for own taxes, US-only tested. Acceptance: tax lots reconcile to total PnL within $0.01; CSV opens cleanly in Excel.

### D4. External signal integration

> Add inputs the council recommends for edge: (1) Twitter/X sentiment scraping (use the `twscrape` library, no API key) keyed to specific tickers for Kalshi and specific tokens for copy-trade; (2) on-chain flow data via Hyperliquid's public funding rates + open interest; (3) macro calendar (FOMC, NFP, CPI dates) that auto-engages caution mode (reduced sizing) within ±2 hours of event. Persist all signals to a new `external_signals` table. Wire into existing scoring/evidence pipelines. Acceptance: each source has its own ingestion CLI command; tests use mocked responses.

---

## Recommended execution order (for the June 22 deadline)

1. **Day 1 (today):** Start polling `copy-trade poll-leaderboard` daily NOW to build snapshot history. Prompt **A1** (testnet round-trip) — gates everything else.
2. **Day 2:** Prompt **A4** (encrypted keys) + **A3** (promotion gates).
3. **Day 3:** Prompt **A2** (Kalshi live POST + worker) — parallel path.
4. **Day 4-5:** Prompts **B1-B4** (risk + alerts + event log).
5. **Day 6:** First testnet `--live` round-trip; verify alerts fire.
6. **Day 7:** Prompts **C1-C2** (validation rigor).
7. **Day 8-9:** Watch paper trades; check Spearman corr. Tune if needed.
8. **Day 10:** If promotion gates green, mainnet `--live` with $200 capital.
9. **Day 11 (June 22):** Scale up if validation holds.

Total prompts: **15**. Each is self-contained for a fresh Claude Code session with no prior context required.
