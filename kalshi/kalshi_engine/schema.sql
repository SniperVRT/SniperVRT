-- Kalshi mispricing engine — storage schema.
-- Every scan, signal, rejection, trade, and outcome is logged so we can
-- compare predicted probabilities to reality.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS markets (
    ticker              TEXT PRIMARY KEY,
    event_ticker        TEXT,
    series_ticker       TEXT,
    title               TEXT,
    subtitle            TEXT,
    category            TEXT,
    status              TEXT,
    rules_primary       TEXT,
    rules_secondary     TEXT,
    open_time           TEXT,
    close_time          TEXT,
    expected_expiration_time TEXT,
    settlement_source   TEXT,
    raw_json            TEXT,
    first_seen_at       TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_markets_close ON markets(close_time);
CREATE INDEX IF NOT EXISTS idx_markets_category ON markets(category);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT NOT NULL REFERENCES markets(ticker),
    captured_at     TEXT NOT NULL,
    yes_bid         INTEGER,         -- cents 0-100
    yes_ask         INTEGER,
    no_bid          INTEGER,
    no_ask          INTEGER,
    last_price      INTEGER,
    volume          INTEGER,
    volume_24h      INTEGER,
    open_interest   INTEGER,
    liquidity       INTEGER,
    yes_book_json   TEXT,            -- list of [price, size]
    no_book_json    TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshots_ticker_time
    ON market_snapshots(ticker, captured_at);

CREATE TABLE IF NOT EXISTS news_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    url             TEXT,
    headline        TEXT,
    body            TEXT,
    published_at    TEXT,
    ingested_at     TEXT NOT NULL,
    tags_json       TEXT
);

CREATE TABLE IF NOT EXISTS source_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name     TEXT NOT NULL,
    captured_at     TEXT NOT NULL,
    payload_json    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS probability_estimates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT NOT NULL REFERENCES markets(ticker),
    strategy        TEXT NOT NULL,
    implied_prob    REAL NOT NULL,
    fair_prob       REAL NOT NULL,
    confidence      REAL NOT NULL,
    uncertainty_lo  REAL NOT NULL,
    uncertainty_hi  REAL NOT NULL,
    edge            REAL NOT NULL,
    expected_value  REAL NOT NULL,
    evidence_json   TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT NOT NULL REFERENCES markets(ticker),
    side            TEXT NOT NULL CHECK (side IN ('yes','no')),
    strategy        TEXT NOT NULL,
    fair_prob       REAL NOT NULL,
    implied_prob    REAL NOT NULL,
    edge            REAL NOT NULL,
    expected_value  REAL NOT NULL,
    confidence      REAL NOT NULL,
    max_price_cents INTEGER NOT NULL,
    suggested_size_usd REAL NOT NULL,
    thesis          TEXT NOT NULL,
    evidence_json   TEXT,
    status          TEXT NOT NULL CHECK (status IN ('pending','approved','rejected','executed','expired')),
    created_at      TEXT NOT NULL,
    decided_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals(ticker);

CREATE TABLE IF NOT EXISTS rejected_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT NOT NULL,
    strategy        TEXT NOT NULL,
    reason          TEXT NOT NULL,
    detail_json     TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rejected_ticker ON rejected_signals(ticker);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id       INTEGER REFERENCES signals(id),
    ticker          TEXT NOT NULL,
    side            TEXT NOT NULL,
    action          TEXT NOT NULL CHECK (action IN ('buy','sell')),
    qty             INTEGER NOT NULL,
    fill_price_cents INTEGER NOT NULL,
    fees_usd        REAL NOT NULL DEFAULT 0,
    mode            TEXT NOT NULL CHECK (mode IN ('signal','paper','live')),
    external_order_id TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT NOT NULL,
    side            TEXT NOT NULL,
    qty             INTEGER NOT NULL,
    avg_cost_cents  INTEGER NOT NULL,
    opened_at       TEXT NOT NULL,
    closed_at       TEXT,
    realized_pnl_usd REAL,
    UNIQUE(ticker, side, opened_at)
);

CREATE TABLE IF NOT EXISTS risk_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT NOT NULL,
    detail          TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pnl_history (
    day             TEXT PRIMARY KEY,    -- YYYY-MM-DD
    realized_usd    REAL NOT NULL DEFAULT 0,
    fees_usd        REAL NOT NULL DEFAULT 0,
    trade_count     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS resolutions (
    ticker          TEXT PRIMARY KEY REFERENCES markets(ticker),
    outcome         TEXT NOT NULL CHECK (outcome IN ('yes','no','void')),
    resolved_at     TEXT NOT NULL,
    settle_price    INTEGER,
    payload_json    TEXT
);

CREATE TABLE IF NOT EXISTS model_performance (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy        TEXT NOT NULL,
    window_start    TEXT NOT NULL,
    window_end      TEXT NOT NULL,
    n               INTEGER NOT NULL,
    brier           REAL,
    avg_edge        REAL,
    avg_realized    REAL,
    profit_factor   REAL,
    max_drawdown    REAL
);

-- ------------------------------------------------------------------- --
-- Week-2 additions: quality, resolution intelligence, ensemble votes, --
-- paper trading, approvals, daily reports.                            --
-- ------------------------------------------------------------------- --

CREATE TABLE IF NOT EXISTS market_quality (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker            TEXT NOT NULL REFERENCES markets(ticker),
    captured_at       TEXT NOT NULL,
    spread_score      REAL NOT NULL,
    liquidity_score   REAL NOT NULL,
    depth_score       REAL NOT NULL,
    volume_score      REAL NOT NULL,
    freshness_score   REAL NOT NULL,
    tradability_score REAL NOT NULL,
    rules_score       REAL NOT NULL,
    total_score       REAL NOT NULL,
    flags_json        TEXT
);
CREATE INDEX IF NOT EXISTS idx_quality_ticker ON market_quality(ticker, captured_at);

CREATE TABLE IF NOT EXISTS resolution_classifications (
    ticker            TEXT PRIMARY KEY REFERENCES markets(ticker),
    risk_class        TEXT NOT NULL,    -- LOW_RISK_OBJECTIVE | MEDIUM_RISK_INTERPRETIVE | HIGH_RISK_AMBIGUOUS | REJECT_SUBJECTIVE | REJECT_SOURCE_UNCLEAR
    risk_score        REAL NOT NULL,    -- 0..1, higher = worse
    ambiguity_flags_json TEXT,
    source_url        TEXT,
    source_name       TEXT,
    cutoff_at         TEXT,
    classified_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ensemble_votes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker            TEXT NOT NULL REFERENCES markets(ticker),
    votes_json        TEXT NOT NULL,    -- [{strategy, fair, conf, lo, hi}, ...]
    combined_fair     REAL NOT NULL,
    combined_confidence REAL NOT NULL,
    agreement_score   REAL NOT NULL,    -- 0..1, 1 = perfect agreement
    implied_prob      REAL NOT NULL,
    edge              REAL NOT NULL,
    expected_value    REAL NOT NULL,
    recommendation    TEXT NOT NULL,    -- yes | no | hold
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ensemble_ticker ON ensemble_votes(ticker, created_at);

CREATE TABLE IF NOT EXISTS paper_orders (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id         INTEGER REFERENCES signals(id),
    ensemble_id       INTEGER REFERENCES ensemble_votes(id),
    ticker            TEXT NOT NULL,
    side              TEXT NOT NULL CHECK (side IN ('yes','no')),
    action            TEXT NOT NULL CHECK (action IN ('buy','sell')),
    qty               INTEGER NOT NULL,
    limit_price_cents INTEGER NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('open','filled','partial','cancelled','rejected','expired')),
    strategy          TEXT,
    notes             TEXT,
    created_at        TEXT NOT NULL,
    decided_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_paper_orders_status ON paper_orders(status);

CREATE TABLE IF NOT EXISTS paper_fills (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id          INTEGER NOT NULL REFERENCES paper_orders(id),
    qty               INTEGER NOT NULL,
    price_cents       INTEGER NOT NULL,
    fee_usd           REAL NOT NULL DEFAULT 0,
    filled_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_positions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker            TEXT NOT NULL,
    side              TEXT NOT NULL CHECK (side IN ('yes','no')),
    qty               INTEGER NOT NULL,
    avg_cost_cents    INTEGER NOT NULL,
    last_mark_cents   INTEGER,
    opened_at         TEXT NOT NULL,
    closed_at         TEXT,
    realized_pnl_usd  REAL,
    strategy          TEXT,
    signal_id         INTEGER REFERENCES signals(id),
    UNIQUE(ticker, side, opened_at)
);
CREATE INDEX IF NOT EXISTS idx_paper_positions_open ON paper_positions(ticker, closed_at);

CREATE TABLE IF NOT EXISTS paper_pnl (
    day               TEXT PRIMARY KEY,
    realized_usd      REAL NOT NULL DEFAULT 0,
    unrealized_usd    REAL NOT NULL DEFAULT 0,
    fees_usd          REAL NOT NULL DEFAULT 0,
    trade_count       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS paper_trade_journal (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker            TEXT NOT NULL,
    event_type        TEXT NOT NULL,   -- open | mark | exit_take_profit | exit_stop | exit_time | exit_close | exit_manual | exit_resolution
    payload_json      TEXT NOT NULL,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_paper_journal_ticker ON paper_trade_journal(ticker, created_at);

CREATE TABLE IF NOT EXISTS approvals (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id         INTEGER REFERENCES signals(id),
    decision          TEXT NOT NULL CHECK (decision IN ('approve','reject','watchlist')),
    actor             TEXT,
    notes             TEXT,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_reports (
    day               TEXT PRIMARY KEY,
    payload_json      TEXT NOT NULL,
    generated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    pages_fetched     INTEGER DEFAULT 0,
    markets_fetched   INTEGER DEFAULT 0,
    markets_persisted INTEGER DEFAULT 0,
    signals_emitted   INTEGER DEFAULT 0,
    rejections        INTEGER DEFAULT 0,
    http_errors       INTEGER DEFAULT 0,
    notes             TEXT
);
