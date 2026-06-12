# Kalshi Mispricing Engine — Handoff, Council Review & Roadmap

This document is the durable handoff for the next Claude Code session.
Part 1 is a copy-paste prompt to seed that session with full context.
Parts 2-4 are the council review, gap analysis vs winning prediction-
market bot archetypes, and the concrete roadmap to reach that level.

---

## Part 1 — Handoff prompt for the next session

> Copy everything between the `---PROMPT---` markers and paste it as the
> first user turn of a fresh Claude Code session opened against the
> Xeno-Bot repo (or whichever repo you mirrored this code into).

---PROMPT---

You are picking up active development of a Kalshi probability mispricing
engine. The codebase is already operational. Do not rebuild what exists.
Read this brief, read `kalshi/docs/HANDOFF_AND_COUNCIL_REVIEW.md` in
full, then `git ls-files kalshi/` and skim the module headers before
proposing changes. Be token-aware and implementation-aware. Do not add
speculative abstractions, neural nets, agent swarms, or cosmetic UI work
unless explicitly requested. Every new module must directly improve
edge detection, execution realism, operational governance, calibration
quality, survivability, or expansion capacity.

What already exists (do not re-implement):

Pipeline: fetch -> filter -> quality score -> resolution-rule
classification -> strategy voting -> ensemble probability estimate ->
per-trade risk gate -> portfolio governance gate -> paper execution ->
persistence -> reporting -> health checks -> drift tracking.

Modules:
- `connectors/kalshi.py` — hardened REST client (RSA-signed auth, retry
  with exponential backoff + jitter on 429/5xx/network, malformed-payload
  defence, paginated market iteration, orderbook fetch).
- `core/probability.py` — pure math: implied prob, midpoint, EV-after-
  fees, edge, capped Kelly, uncertainty-band overlap.
- `core/filters.py` — status, spread cap, 24h volume floor, time-to-close
  window, blocked categories, vague-rules keywords.
- `core/quality.py` — 7-component market quality score (spread, liquidity,
  depth, volume, freshness, tradability, rules) with flags.
- `core/resolution.py` — deterministic rule classifier: LOW_RISK_OBJECTIVE
  / MEDIUM_RISK_INTERPRETIVE / HIGH_RISK_AMBIGUOUS / REJECT_SUBJECTIVE /
  REJECT_SOURCE_UNCLEAR plus risk_score, source/cutoff extraction.
- `core/ensemble.py` — weighted strategy combiner with agreement scoring.
- `strategies/{news_lag,mean_reversion,rules_mispricing}.py` — three
  base strategies, all consume optional structured evidence and return
  a `FairEstimate(fair_prob, confidence, uncertainty_lo, uncertainty_hi)`.
- `news/{feeds,evidence}.py` — stdlib RSS+Atom parser, SHA-1 dedup,
  per-source reliability, stopword-filtered keyword extraction, per-
  market attachment with relevance scoring.
- `paper/executor.py` — realistic paper trading: order-book walks,
  multi-level fills, fee modelling, journaled exits (take_profit,
  stop_loss, time_based, edge_decay, market_close, resolution, manual).
- `risk/rules.py` — per-trade gate: max position, max open positions,
  daily/weekly loss stops, post-loss cooldown, no averaging down.
- `risk/governance.py` — portfolio-level gate: bankroll, realised /
  unrealised PnL, open exposure, peak equity, drawdown, category &
  strategy exposure, consecutive-loss streak. Nine named locks
  (stale_data, repeat_failure, reconcile_fail, emergency, db_corruption,
  portfolio_drawdown, consecutive_losses, exposure_cap, duplicate_order).
- `execution/live.py` — disabled-by-default live adapter with preview,
  submit, cancel, reconcile_order. Real submit requires ALL of:
  live_enabled=true, live_dry_run=false, manual-approval row present,
  no active locks, no duplicate client_order_id.
- `rehearsal/engine.py` — 7-scenario dry-run rehearsal with audit JSON.
- `validation/{calibration,metrics,drift}.py` — Brier, log loss, profit
  factor, drawdown, win rate, expectancy, calibration buckets, paper-
  vs-live drift.
- `ops/health.py` — db, scanner freshness, news freshness, ingestion
  runs, exposure, locks. Auto-engages governance locks on failure.
- `reports/daily.py` — rolling 24h operational report.
- `dashboard.py` — Streamlit ops surface with 10 pages.
- `cli.py` — Typer CLI with 14 commands.
- `scripts/readiness_report.py` — autonomous LIVE_READY/LIVE_LOCKED
  verdict generator.

Storage: SQLite WAL with ~30 tables covering every market, snapshot,
news item, evidence attachment, probability estimate, signal, rejection,
trade, position, risk event, lock, portfolio snapshot, rehearsal run,
drift report, health check, and ingestion run.

Tests: 93 passing pytest cases (probability math, filters, quality,
resolution, ensemble, paper trading, governance, live execution
(approval-required, duplicate prevention, reconciliation), rehearsal
(no-leftover-locks), drift, health, news parsing/dedup/keywords/
attachment, CLI registration, dashboard compile, all-modules importable,
end-to-end scanner integration).

Defaults are intentionally conservative: nano bankroll ($100 / $5 max
position / 3 open / $10 daily stop), MIN_EDGE=5%, MIN_CONFIDENCE=0.6,
execution_mode=signal, live_enabled=false, live_dry_run=true,
live_require_manual_approval=true.

Operational status: a fresh DB correctly reports LIVE_LOCKED until real
market snapshots populate, real news is ingested, calibration buckets
have data, and the operator explicitly flips the live env flags.

Read `kalshi/docs/HANDOFF_AND_COUNCIL_REVIEW.md` (Part 2 council review,
Part 3 gap analysis, Part 4 roadmap) before starting. The roadmap is
ordered by leverage. Pick the first unblocked item, ask me to confirm
scope if it spans multiple modules, then implement with tests.

Do NOT do: rewrite stable modules, change default risk caps, add LLM
calls for things deterministic code already solves, build a giant agent
framework, add neural nets before the statistical baseline exists.

Token discipline: prefer reading specific lines over whole files,
edit over rewrite, batch independent tool calls, no narration of
internal deliberation.

---PROMPT---

---

## Part 2 — Council Review

Five expert perspectives reviewing the current system. Each gives a
short read, the top gaps from their lens, and concrete high-leverage
recommendations.

### Council Member 1 — Quantitative Trader (Strategy & Edge)

**What's solid.** The probability framework is mathematically clean.
EV-after-fees is computed correctly, capped Kelly sizing is sane, the
uncertainty-band overlap check catches the common "spurious signal"
trap that kills naïve bots. Three diverse strategy modules with
ensemble weighting + agreement scoring is a more honest design than
most retail bots.

**Gaps.**
- No statistical baseline model. Every winning prediction-market
  operator has a logistic regression / gradient-boosted classifier
  trained on resolved markets as a baseline. Without one, every
  strategy is fighting blind against the market's collective prior.
- Ensemble priors (`DEFAULT_PRIORS` in `core/ensemble.py`) are hand-set
  constants. They should be learned from realised calibration per
  strategy.
- No market-making strategy. The cleanest profit source on Kalshi for
  liquid daily/weekly markets is posting both sides and capturing
  spread. Currently we only consume liquidity.
- No cross-market constraint enforcement. If markets A, B, and
  "A and B" exist, their prices must satisfy P(A∩B) ≤ min(P(A), P(B)).
  Violations are pure-arb edge.
- The three strategies all need external evidence to fire. In the
  default config running a scan with no news layer feeding evidence,
  most strategies output nothing.

**Top 3 recommendations.**
1. Add `strategies/baseline_logit.py`: train logistic regression on
   features (current price, time-to-close, category, historical
   resolution rate of similar markets, quality score) using past
   resolutions. Use as ensemble member with its own confidence band.
2. Add `strategies/market_maker.py`: post passive YES bid and NO bid
   on markets where (spread × volume) > (estimated adverse selection
   cost). Quote width tied to per-market vol.
3. Add `core/constraints.py`: detect related markets (same
   event_ticker / series_ticker), enforce probability axioms, flag
   violations as signals.

### Council Member 2 — Market Microstructure Specialist

**What's solid.** Paper executor walks the actual order book level by
level, models fees, and handles partial fills. Limit-price discipline
is enforced. Idempotent client_order_id, duplicate-order lock,
reconciliation path — all the structural pieces are present.

**Gaps.**
- REST polling for market data. Latency between a real price change
  and the bot's awareness is bounded by scan frequency, which makes
  the entire news-lag strategy uncompetitive against anyone reading
  the WebSocket. Kalshi exposes `wss://api.elections.kalshi.com/
  trade-api/ws/v2` with `orderbook_snapshot`, `orderbook_delta`,
  `trade`, `ticker` channels — they are the canonical feed.
- No queue-position tracking. When the bot posts a passive order it
  has no model of where it sits in the FIFO queue, so it cannot
  estimate fill probability.
- No order-type variety. Live adapter only knows "limit". Real
  competitive execution needs post-only (rebate, prevents adverse
  selection), IOC (sweep liquidity then cancel), and FOK (all or
  none).
- Fee model is a single conservative formula. Kalshi has changed its
  fee schedule multiple times and applies different rates by market
  type. A real fee table keyed by series + price needs to exist and
  needs a unit test against published examples.
- Slippage model in the paper executor doesn't include the queue-
  push effect (your own order moves the book) or the time-delay
  effect (book changes between scan and submit).

**Top 3 recommendations.**
1. Add `connectors/kalshi_ws.py` and a `streaming/` module that
   maintains an in-memory order book per ticker from WebSocket
   deltas. Scanner reads from memory, not REST.
2. Replace the single-formula fee estimator with a `core/fees.py`
   module that loads the published Kalshi schedule (per series) and
   has a `pytest` test against published worked examples.
3. Extend `execution/live.py` with `post_only=True/False` and
   `time_in_force=GTC/IOC/FOK` plumbed through to the eventual
   signed POST.

### Council Member 3 — Risk Manager

**What's solid.** Two-layer risk model (per-trade + portfolio), nine
named locks with explicit reasons, drawdown tracking, consecutive-loss
streak, exposure caps by category and strategy, manual approval
required by default. The system errs heavily toward LIVE_LOCKED. This
is correct.

**Gaps.**
- No correlation cap. Three markets on "Fed raises by 25bps", "Fed
  holds", "Fed cuts" are mutually exclusive and you can have
  meaningful exposure across all three without tripping the cap —
  effective exposure is ≤ max(positions), but our tracker treats it
  as the sum. Conversely, three independent positions on "weather in
  city X" are perfectly correlated risk and we treat them as
  diversified.
- No bankroll-ruin Monte Carlo. We have caps but no simulation of
  "given current edge distribution and bet sizes, what is the
  probability of hitting the $25 drawdown lock within 50 trades".
- No vol targeting / regime detection. Bet size is `min(Kelly,
  max_position)` regardless of recent realised vol of the bot's
  PnL.
- $5 default max position is fine for nano-bankroll but should be
  REDUCED until calibration buckets have ≥100 resolved samples per
  strategy. Right now the system would accept full-cap bets on day
  one against zero historical evidence its predictions are
  calibrated.
- No "max trades per event-day" cap. If a single news event
  generates 20 correlated signals across related markets, we'd open
  the first 3 (max_open_positions=3) but the next scan re-opens
  more as positions close. A per-event cooldown would catch this.

**Top 3 recommendations.**
1. Add `risk/correlation.py` that groups open positions by
   event_ticker / series_ticker / category, computes effective
   exposure under "all mutually exclusive" vs "all perfectly
   correlated" assumptions, takes the worst case, and caps from
   that.
2. Add `scripts/ruin_simulation.py`: Monte Carlo over current edge
   + sizing distribution to estimate P(ruin) and P(hit drawdown
   lock). Block live mode if P > 5%.
3. Add a calibration gate to `execution/live.py.submit`: refuse
   live orders for any strategy with fewer than N resolved samples
   (config knob, default 50).

### Council Member 4 — ML / Data Engineer

**What's solid.** Every estimate, evidence item, outcome, and risk
event is persisted with timestamps. The 13-table audit schema is more
disciplined than most production trading systems. Calibration buckets
are computed correctly. Drift tracking exists.

**Gaps.**
- The system never learns from its own data. There is no feedback
  loop from `resolutions` back into strategy weights, calibration
  adjustment, or feature thresholds. It is open-loop.
- No isotonic regression / Platt scaling layer between raw fair_prob
  and the version used for sizing. If the bot consistently outputs
  60% for markets that resolve YES 52% of the time, sizing should be
  derated automatically.
- No walk-forward backtest. We have `market_snapshots` accumulating
  over time but no harness that replays them through the pipeline
  with hindsight blinded to evaluate counterfactual performance.
- No feature store. Each strategy re-derives features from raw
  market dicts every scan. This makes it impossible to ablation-test
  features or measure feature drift.
- No model versioning. If we change the news_lag heuristic, the old
  estimates and the new ones get co-mingled in `probability_estimates`
  with the same `strategy` value. Calibration analysis can't tell
  them apart.
- No A/B framework. We have no way to run "strategy V2" in shadow
  alongside "strategy V1" without contaminating real signal flow.

**Top 3 recommendations.**
1. Add `validation/calibration_adjust.py`: fit isotonic regression
   per strategy from resolved data, apply on top of raw fair_prob
   before edge computation.
2. Add `backtest/walk_forward.py`: replay historical snapshots
   chronologically, apply current strategies, compute per-day PnL +
   calibration, output a report. Block any new strategy from
   shipping live until it passes a walk-forward minimum.
3. Add a `model_version` column to `probability_estimates` and
   `signals`, default to the git short SHA at module import. Tag
   every estimate with the code version that produced it.

### Council Member 5 — Systems / SRE

**What's solid.** SQLite WAL mode for concurrent reads, structured
logging via structlog, retry+backoff on the HTTP layer, idempotent
INSERT-or-UPDATE for markets, manual lock/unlock controls, comprehensive
schema. The codebase compiles and tests cleanly.

**Gaps.**
- Nothing runs continuously. APScheduler is in `pyproject.toml` but
  never imported. The scanner only runs when a human invokes the
  CLI. A real bot needs a worker process that wakes every N seconds.
- No metrics surface. Health checks exist but no Prometheus exposition
  endpoint, no Grafana dashboard, no alerting on emergency_lock.
- No deployment story. There's no Dockerfile, no systemd unit, no
  GitHub Actions, no VPS setup script. A real operator can't deploy
  this without bespoke work.
- Secrets management. The Kalshi private key path is loaded from an
  env-var default. No support for a real secrets backend (Vault,
  AWS SM, sops, etc.) and no rotation.
- No log shipping. structlog emits to stdout; nothing aggregates
  across multiple processes or persists logs beyond container
  lifetime.
- SQLite scaling cliff. Single-writer constraint becomes a problem
  the moment you have (a) a scanner process, (b) a WebSocket
  listener, and (c) a worker for paper execution all wanting to
  write. The migration path to Postgres needs to be planned now
  rather than during an incident.
- No alembic-style migrations. Every schema change is "add new
  CREATE TABLE IF NOT EXISTS" — works for the current additive
  evolution but breaks the moment a column needs to change type.
- `kalshi/data/` ignores aren't there yet — operator data ends up
  inside the repo working tree.

**Top 3 recommendations.**
1. Add `kalshi/worker.py`: APScheduler-driven loop running scan
   (every 60s), news-ingest (every 5min), health-check (every 30s),
   portfolio snapshot (every 5min), daily-report (once per day).
   Single process, structured logs, signal-handled graceful
   shutdown.
2. Add `kalshi/metrics.py`: Prometheus exposition via
   `prometheus-client`, expose counters/gauges for signals_emitted,
   rejections_by_reason, paper_pnl_realized, governance_locks, last
   scan duration, last news fetch age. Add a `metrics` CLI command
   that exposes them via a simple FastAPI endpoint.
3. Add `Dockerfile` + `docker-compose.yml` (worker + dashboard) +
   `infra/systemd/` units for VPS deployment. Document the deploy
   in `kalshi/docs/DEPLOY.md`.

---

## Part 3 — Gap analysis vs winning prediction-market bot archetypes

There is little public information about specific winning Kalshi bots
(operators have no incentive to publish), but the broader prediction-
market and event-driven-trading literature converges on a clear set of
patterns from sportsbook arbitrage, Polymarket statistical operators,
and equity microstructure. Below: what those systems have that we
don't, sorted by realistic implementation cost.

### Foundational (every winning operator has these)

| Capability | Our state | Gap |
|---|---|---|
| WebSocket market data | REST poll | High — news strategies effectively can't compete |
| Continuous worker loop | CLI-only | High — bot doesn't run autonomously |
| Statistical baseline model | None | High — every strategy fights blind |
| Calibration adjustment layer | Buckets only | Medium — sizing isn't calibration-aware |
| Walk-forward backtest | None | Medium — can't validate new strategies safely |
| Production observability | logs only | Medium — no alerts on incidents |
| Live order placement | Stubbed | High — adapter never actually dispatches |

### Differentiating (separates good from winning)

| Capability | Our state | Gap |
|---|---|---|
| Cross-market constraint arb | None | High — pure-arb edge sitting on the table |
| Market making | None | Medium — different strategy class entirely |
| Settlement-source automation | None | High — direct BLS/NOAA/EIA feeds beat news to market |
| Multi-venue (Kalshi+Polymarket) | None | Low priority — defer to phase 4 |
| Realtime NLP on news | Keyword overlap | Medium — keyword scoring is the floor |
| Order-book pressure model | None | Medium — execution quality material |
| Feature store + drift detection | None | Low priority until baseline model exists |

### Operator-grade (mature trading firms)

| Capability | Our state | Gap |
|---|---|---|
| Kill switch reachable from phone | Lock CLI | Medium — needs an HTTP endpoint |
| PagerDuty / Slack alerting | None | Medium |
| CI/CD pipeline | None | Low — single operator likely fine without |
| Postgres + multiple writers | SQLite | Defer until ≥2 writers needed |
| Disaster recovery (DB backups) | None | Medium — `litestream` is the cheap answer |

---

## Part 4 — Roadmap to "winning bot" level

Ordered by leverage × confidence. Each item is independently shippable
and gated by tests. Estimates assume one focused Claude Code session
each (1–4 hours of attention) unless noted.

### Phase 4A — Make the bot actually run (week 4)

1. **WebSocket market data** (`connectors/kalshi_ws.py`,
   `streaming/book.py`). Maintain in-memory books per ticker.
   Scanner reads from memory. Persist deltas async to SQLite.
   → Unlocks meaningful news-lag latency, queue tracking, real
   microstructure analysis.

2. **Continuous worker** (`kalshi/worker.py` with APScheduler).
   Scan 60s, news 5min, health 30s, daily-report 24h. Single
   process, graceful shutdown. → Bot is no longer a CLI demo.

3. **Real Kalshi signed POST** for `/portfolio/orders` and
   `/portfolio/orders/{order_id}` (cancel). Plumb post_only and
   time_in_force. → Live adapter is no longer a stub.

4. **Fee schedule overhaul** (`core/fees.py` with a published table
   + unit tests). → Removes a known source of EV miscalculation.

### Phase 4B — Make the bot learn (week 5)

5. **Statistical baseline model** (`strategies/baseline_logit.py`).
   Logistic regression on resolved markets. Joins the ensemble.
   → Every market gets a non-zero estimate even with no news.

6. **Isotonic calibration** (`validation/calibration_adjust.py`).
   Wrap raw fair_prob in per-strategy isotonic adjustment before
   edge computation. → Sizing becomes calibration-aware.

7. **Walk-forward backtest** (`backtest/walk_forward.py`). Replay
   historical snapshots. Gate new strategies on minimum walk-
   forward performance. → Can validate strategies safely before
   shipping live.

8. **Model version stamping**. Add `model_version` column to
   `probability_estimates` and `signals`. Tag with git short SHA.
   → Calibration analysis stops co-mingling old and new estimates.

### Phase 4C — Edge sources that don't exist yet (week 6)

9. **Cross-market constraint detection** (`core/constraints.py`).
   Group by event_ticker, enforce probability axioms, flag
   violations as signals.

10. **Market-making strategy** (`strategies/market_maker.py`).
    Quote both sides on markets where spread > adverse selection
    cost. Different strategy class — different risk profile.

11. **Direct settlement-source feeds** (`connectors/sources/{bls,
    noaa,eia,treasury}.py`). Fetch directly from official APIs and
    map readings to relevant Kalshi markets. → Beat news-driven
    competitors to the price update.

12. **Correlation-aware exposure** (`risk/correlation.py`). Group
    open positions by event/series, take worst-case effective
    exposure, cap from there.

### Phase 4D — Operator-grade (week 7+)

13. **Prometheus metrics** + a tiny FastAPI `/metrics` endpoint
    + a Grafana dashboard JSON in `infra/grafana/`.

14. **Kill switch HTTP endpoint** — POST `/admin/lock` with auth
    that engages emergency lock. Phone-accessible.

15. **Dockerfile + docker-compose** (worker + dashboard +
    optional Grafana). VPS deploy doc in `kalshi/docs/DEPLOY.md`.

16. **litestream backups** for SQLite -> S3/B2. Disaster recovery
    < $1/month.

17. **Plan SQLite -> Postgres** migration. Don't migrate yet;
    document the schema-translation script so it's executable when
    needed.

### Phase 4E — Frontier (week 8+, only if 4A-D land cleanly)

18. **Realtime NLP on news**: replace keyword overlap with a tiny
    classifier (DistilBERT or local LLM) routing headlines to
    relevant markets in sub-second.

19. **Polymarket connector** in mirror-image of Kalshi connector —
    enables cross-venue spread arb on duplicated markets.

20. **Monte Carlo bankroll simulator** for live-readiness gating.

### Stop-the-line checks before going live

The bot should not move beyond paper trading until ALL of:

- [ ] Phase 4A complete (WebSocket, worker, signed live POST, fees)
- [ ] ≥ 100 resolved markets per active strategy in calibration buckets
- [ ] Phase 4B item 6 complete (isotonic calibration layer applied)
- [ ] Phase 4B item 7 complete (walk-forward shows positive expectancy
      net of fees on out-of-sample data)
- [ ] Phase 4C item 12 complete (correlation cap)
- [ ] Drift report shows |avg_slippage_cents| < 1.0 across ≥ 50
      paper trades
- [ ] Operator kill switch tested end-to-end
- [ ] Disaster recovery tested: kill DB, restore from backup,
      verify rehearsal still passes

---

## Maintenance notes

- This document is the source of truth for "what's next". Update it
  when items ship. Tick the gate checklist as items land.
- The handoff prompt in Part 1 should be regenerated when major
  modules are added or removed.
- Each phase item should produce its own PR with tests, ideally
  small enough to review in one sitting.
