-- Copy-trade meta-allocator schema.
-- Every master snapshot, allocation decision, subscription, and PnL record
-- is stored here to support post-hoc ranking validation and survivorship analysis.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---- Master traders --------------------------------------------------------
CREATE TABLE IF NOT EXISTS masters (
    uid             TEXT PRIMARY KEY,           -- platform-assigned uid / trader_id
    platform        TEXT NOT NULL,              -- 'bybit' | 'bitget' | 'binance'
    nickname        TEXT,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    active          INTEGER NOT NULL DEFAULT 1  -- still visible on leaderboard?
);

-- ---- Per-poll performance snapshots ----------------------------------------
-- Kept forever for survivorship analysis: traders that fall off the leaderboard
-- still have their history preserved.
CREATE TABLE IF NOT EXISTS trader_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    master_uid      TEXT NOT NULL REFERENCES masters(uid),
    captured_at     TEXT NOT NULL,
    roi_7d          REAL,                       -- % return last 7 days
    roi_30d         REAL,                       -- % return last 30 days
    roi_all         REAL,                       -- % return since inception
    mdd             REAL,                       -- max drawdown % (negative number)
    win_rate        REAL,                       -- fraction 0-1
    total_trades    INTEGER,
    followers       INTEGER,
    aum_usdt        REAL,                       -- assets under management
    avg_holding_h   REAL,                       -- avg hold time (hours)
    sharpe          REAL,                       -- if platform provides it
    raw_json        TEXT                        -- full platform response
);

CREATE INDEX IF NOT EXISTS idx_ts_uid_time ON trader_snapshots(master_uid, captured_at);

-- ---- Computed scores (one per snapshot) ------------------------------------
CREATE TABLE IF NOT EXISTS trader_scores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    master_uid      TEXT NOT NULL REFERENCES masters(uid),
    snapshot_id     INTEGER NOT NULL REFERENCES trader_snapshots(id),
    scored_at       TEXT NOT NULL,
    sharpe_est      REAL,                       -- our estimated Sharpe
    calmar_est      REAL,                       -- annualized_return / abs(mdd)
    composite_score REAL NOT NULL,              -- final weighted score
    rank_at_time    INTEGER,                    -- rank within scoring run
    eligible        INTEGER NOT NULL DEFAULT 1, -- passed all threshold filters?
    filter_reason   TEXT                        -- why excluded if not eligible
);

CREATE INDEX IF NOT EXISTS idx_scores_uid ON trader_scores(master_uid, scored_at);

-- ---- Allocation decisions --------------------------------------------------
CREATE TABLE IF NOT EXISTS allocations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    decided_at      TEXT NOT NULL,
    master_uid      TEXT NOT NULL REFERENCES masters(uid),
    allocated_usdt  REAL NOT NULL,
    allocation_pct  REAL NOT NULL,
    score           REAL NOT NULL,
    action          TEXT NOT NULL CHECK (action IN ('subscribe','keep','rebalance','unsubscribe'))
);

CREATE INDEX IF NOT EXISTS idx_alloc_uid ON allocations(master_uid, decided_at);

-- ---- Active subscriptions --------------------------------------------------
CREATE TABLE IF NOT EXISTS subscriptions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    master_uid      TEXT NOT NULL REFERENCES masters(uid),
    platform        TEXT NOT NULL,
    mode            TEXT NOT NULL DEFAULT 'live'
                        CHECK (mode IN ('live','paper','backtest')),
    backtest_run_id INTEGER,                    -- non-null only for mode='backtest'
    allocated_usdt  REAL NOT NULL,
    subscribed_at   TEXT NOT NULL,
    unsubscribed_at TEXT,
    status          TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','paused','unsubscribed','error')),
    external_sub_id TEXT,                       -- platform's subscription id
    lockup_until_ms INTEGER,                    -- ms timestamp; can't withdraw before
    entry_equity_usdt REAL,                     -- vault equity at subscribe time (for watchdog)
    error           TEXT
);

CREATE INDEX IF NOT EXISTS idx_subs_uid ON subscriptions(master_uid);
CREATE INDEX IF NOT EXISTS idx_subs_status ON subscriptions(status);

-- ---- Subscription PnL snapshots --------------------------------------------
CREATE TABLE IF NOT EXISTS subscription_pnl (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id INTEGER NOT NULL REFERENCES subscriptions(id),
    captured_at     TEXT NOT NULL,
    realized_pnl    REAL,
    unrealized_pnl  REAL,
    total_pnl       REAL,
    open_positions  INTEGER,
    raw_json        TEXT
);

CREATE INDEX IF NOT EXISTS idx_pnl_sub ON subscription_pnl(subscription_id, captured_at);

-- ---- Portfolio state (rolling) ---------------------------------------------
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at     TEXT NOT NULL,
    total_capital   REAL NOT NULL,
    deployed_usdt   REAL NOT NULL,
    realized_pnl    REAL NOT NULL DEFAULT 0,
    unrealized_pnl  REAL NOT NULL DEFAULT 0,
    peak_equity     REAL NOT NULL,
    drawdown_pct    REAL NOT NULL DEFAULT 0,
    active_masters  INTEGER NOT NULL DEFAULT 0
);

-- ---- Vault pair correlations (for dedup) -----------------------------------
CREATE TABLE IF NOT EXISTS vault_correlations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    vault_a         TEXT NOT NULL,
    vault_b         TEXT NOT NULL,
    correlation     REAL NOT NULL,              -- Pearson on equity-curve returns
    n_points        INTEGER NOT NULL,
    computed_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_corr_pair ON vault_correlations(vault_a, vault_b);

-- ---- Backtest runs ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS backtest_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    params_json     TEXT NOT NULL,              -- weights, thresholds used
    tick_count      INTEGER NOT NULL DEFAULT 0,
    initial_capital REAL NOT NULL,
    final_equity    REAL,
    total_return    REAL,
    sharpe          REAL,
    max_drawdown    REAL,
    avg_alloc_count REAL,
    notes           TEXT
);

-- ---- Validation runs (testnet roundtrips, promotion gates) -----------------
CREATE TABLE IF NOT EXISTS validation_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT NOT NULL,              -- 'testnet_roundtrip' | 'promotion_check'
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    passed_at       TEXT,                       -- non-null only if all steps passed
    details_json    TEXT NOT NULL,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_val_kind ON validation_runs(kind, passed_at);

CREATE TABLE IF NOT EXISTS paper_rebalance_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at          TEXT NOT NULL,
    counts_json     TEXT NOT NULL,
    success         INTEGER NOT NULL DEFAULT 1
);

-- ---- Data quality failures -------------------------------------------------
CREATE TABLE IF NOT EXISTS data_quality_failures (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at     TEXT NOT NULL,
    source          TEXT NOT NULL,              -- 'kalshi_market' | 'hl_vault'
    reason          TEXT NOT NULL,
    raw_json        TEXT
);

-- ---- Event log (structlog persistence, populated by Prompt 4) --------------
CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    level           TEXT NOT NULL,
    logger          TEXT,
    event           TEXT NOT NULL,
    payload_json    TEXT,
    host            TEXT,
    model_version   TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS raw_payloads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at     TEXT NOT NULL,
    source          TEXT NOT NULL,
    request_hash    TEXT,
    payload_json    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payload_src ON raw_payloads(source, captured_at);

-- ---- Sent notifications (idempotency) --------------------------------------
CREATE TABLE IF NOT EXISTS sent_notifications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_hash      TEXT NOT NULL UNIQUE,
    sent_at         TEXT NOT NULL,
    severity        TEXT NOT NULL,
    title           TEXT
);

-- ---- Model versions --------------------------------------------------------
CREATE TABLE IF NOT EXISTS model_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    version         TEXT NOT NULL UNIQUE,
    params_json     TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    notes           TEXT
);

-- ---- Tax lots (FIFO ledger) ------------------------------------------------
CREATE TABLE IF NOT EXISTS tax_lots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    vault_address   TEXT NOT NULL,
    opened_at       TEXT NOT NULL,
    units_usdt      REAL NOT NULL,
    cost_basis_usdt REAL NOT NULL,
    closed_at       TEXT,
    proceeds_usdt   REAL,
    realised_gain   REAL,
    holding_days    INTEGER,
    is_long_term    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_taxlots_vault ON tax_lots(vault_address, opened_at);

-- ---- Portfolio risk snapshots ----------------------------------------------
CREATE TABLE IF NOT EXISTS portfolio_risk_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at     TEXT NOT NULL,
    avg_pairwise_corr REAL,
    max_pairwise_corr REAL,
    n_pairs         INTEGER,
    same_leader_max_pct REAL,
    locked_pct      REAL,
    var_95_usdt     REAL,
    cvar_95_usdt    REAL
);

-- ---- External signals ------------------------------------------------------
CREATE TABLE IF NOT EXISTS external_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at     TEXT NOT NULL,
    source          TEXT NOT NULL,              -- 'twitter' | 'onchain' | 'macro'
    key             TEXT NOT NULL,              -- vault uid or asset
    score           REAL,
    payload_json    TEXT
);

CREATE INDEX IF NOT EXISTS idx_extsig ON external_signals(source, key, captured_at);

-- ---- Subscription entry equity (for watchdog drawdown calc) ----------------
-- Note: column is added via ALTER if upgrading; included here for fresh DBs.

-- ---- Emergency stop log ----------------------------------------------------
CREATE TABLE IF NOT EXISTS safety_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_at        TEXT NOT NULL,
    event_type      TEXT NOT NULL,              -- 'daily_loss_stop' | 'drawdown_stop' | 'manual_stop'
    detail          TEXT,
    resolved_at     TEXT
);
