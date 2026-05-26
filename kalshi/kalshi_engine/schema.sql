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
