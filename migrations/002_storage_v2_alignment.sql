-- Migration 002: Align StateStore schema with StorageV2

CREATE TABLE IF NOT EXISTS matches (
    id TEXT PRIMARY KEY,
    start_time_ns INTEGER,
    match_winner TEXT
);

CREATE TABLE IF NOT EXISTS positions (
    position_id TEXT PRIMARY KEY,
    token_id TEXT,
    match_id TEXT,
    entry_px REAL,
    shares REAL,
    size_usd REAL,
    side TEXT,
    mode TEXT,
    state TEXT,
    raw_json TEXT,
    updated_at_ns INTEGER
);

CREATE TABLE IF NOT EXISTS closed_positions (
    position_id TEXT PRIMARY KEY,
    token_id TEXT,
    match_id TEXT,
    entry_px REAL,
    exit_px REAL,
    shares REAL,
    size_usd REAL,
    pnl_usd REAL,
    side TEXT,
    mode TEXT,
    exit_reason TEXT,
    exit_time_ns INTEGER,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS daily_budgets (
    date_str TEXT NOT NULL,
    mode TEXT NOT NULL,
    total_submitted_usd REAL,
    total_filled_usd REAL,
    open_positions INTEGER,
    daily_realized_pnl_usd REAL,
    submitted_match_sides TEXT,
    submitted_match_usd TEXT,
    submitted_family_usd TEXT,
    updated_at_ns INTEGER,
    PRIMARY KEY (date_str, mode)
);
