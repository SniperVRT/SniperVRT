PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS unified_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at     TEXT NOT NULL,
    kalshi_equity   REAL,
    copytrade_equity REAL,
    total_equity    REAL,
    kalshi_deployed REAL,
    copytrade_deployed REAL,
    total_drawdown_pct REAL,
    combined_locks_json TEXT,
    active_subscriptions_n INTEGER,
    open_signals_n  INTEGER
);

CREATE TABLE IF NOT EXISTS unified_safety_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_at        TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    detail          TEXT,
    resolved_at     TEXT
);
