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
    allocated_usdt  REAL NOT NULL,
    subscribed_at   TEXT NOT NULL,
    unsubscribed_at TEXT,
    status          TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','paused','unsubscribed','error')),
    external_sub_id TEXT,                       -- platform's subscription id
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

-- ---- Emergency stop log ----------------------------------------------------
CREATE TABLE IF NOT EXISTS safety_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_at        TEXT NOT NULL,
    event_type      TEXT NOT NULL,              -- 'daily_loss_stop' | 'drawdown_stop' | 'manual_stop'
    detail          TEXT,
    resolved_at     TEXT
);
