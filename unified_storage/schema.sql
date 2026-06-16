-- data_v2/operational.db schema
-- State tables. Transactional, single-process writer.
-- Run via unified_storage.state.init_db() which executes this file.

PRAGMA journal_mode = WAL;          -- concurrent reads + single writer
PRAGMA synchronous  = NORMAL;       -- safe with WAL, ~10x faster than FULL
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- positions
-- Replaces logs/live_positions.json + logs/positions.csv.
-- One row per opened position; closed positions retained for audit.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS positions (
    position_id        TEXT PRIMARY KEY,
    attempt_id         TEXT,                 -- foreign key into trade_attempts parquet
    signal_id          TEXT,                 -- foreign key into signals parquet
    match_id           TEXT NOT NULL,
    token_id           TEXT NOT NULL,
    side               TEXT NOT NULL,        -- 'YES' | 'NO'
    market_name        TEXT,
    event_type         TEXT,
    opened_at_ns       INTEGER NOT NULL,
    closed_at_ns       INTEGER,              -- NULL while open
    entry_price        REAL NOT NULL,
    shares             REAL NOT NULL,
    notional_usd       REAL NOT NULL,
    exit_price         REAL,
    exit_reason        TEXT,
    realized_pnl_usd   REAL,
    status             TEXT NOT NULL,        -- 'open' | 'closed' | 'partial'
    schema_version     TEXT NOT NULL DEFAULT 'v1.0'
);

CREATE INDEX IF NOT EXISTS idx_positions_status      ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_match_id    ON positions(match_id);
CREATE INDEX IF NOT EXISTS idx_positions_opened_at_ns ON positions(opened_at_ns);


-- ---------------------------------------------------------------------
-- budget
-- Replaces logs/live_state.json. Singleton row (id=1) holding running
-- totals for the risk gates.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS budget (
    id                       INTEGER PRIMARY KEY CHECK (id = 1),
    total_submitted_usd      REAL NOT NULL DEFAULT 0,
    total_filled_usd         REAL NOT NULL DEFAULT 0,
    open_positions_count     INTEGER NOT NULL DEFAULT 0,
    daily_realized_pnl_usd   REAL NOT NULL DEFAULT 0,
    daily_drawdown_usd       REAL NOT NULL DEFAULT 0,
    last_reset_at_ns         INTEGER,
    updated_at_ns            INTEGER NOT NULL,
    schema_version           TEXT NOT NULL DEFAULT 'v1.0'
);

INSERT OR IGNORE INTO budget (id, updated_at_ns) VALUES (1, 0);


-- ---------------------------------------------------------------------
-- market_mappings
-- Replaces the cached lookups currently rebuilt from markets.yaml on
-- every restart. `effective_from`/`effective_to` let us correct mappings
-- historically without losing the original.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS market_mappings (
    market_id          TEXT NOT NULL,
    condition_id       TEXT,
    yes_token_id       TEXT NOT NULL,
    no_token_id        TEXT NOT NULL,
    market_type        TEXT NOT NULL,        -- 'MAP_WINNER' | 'MATCH_WINNER'
    market_name        TEXT,
    yes_team           TEXT,
    no_team            TEXT,
    dota_match_id      TEXT,
    steam_radiant_team TEXT,
    steam_dire_team    TEXT,
    steam_side_mapping TEXT,                  -- 'normal' | 'reversed'
    confidence         REAL,
    effective_from_ns  INTEGER NOT NULL,
    effective_to_ns    INTEGER,               -- NULL while current
    schema_version     TEXT NOT NULL DEFAULT 'v1.0',
    PRIMARY KEY (market_id, effective_from_ns)
);

CREATE INDEX IF NOT EXISTS idx_market_mappings_dota_match ON market_mappings(dota_match_id);
CREATE INDEX IF NOT EXISTS idx_market_mappings_yes_token  ON market_mappings(yes_token_id);
CREATE INDEX IF NOT EXISTS idx_market_mappings_no_token   ON market_mappings(no_token_id);


-- ---------------------------------------------------------------------
-- cache_balance — replaces logs/usdc_balance.json
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cache_balance (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    usdc_balance    REAL NOT NULL,
    fetched_at_ns   INTEGER NOT NULL,
    ttl_sec         INTEGER NOT NULL DEFAULT 60,
    source          TEXT,                     -- e.g. 'poly_clob_api'
    schema_version  TEXT NOT NULL DEFAULT 'v1.0'
);


-- ---------------------------------------------------------------------
-- cache_team_id — replaces logs/team_id_cache.json
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cache_team_id (
    team_name_norm   TEXT PRIMARY KEY,        -- normalized via norm_team()
    team_id          TEXT NOT NULL,
    canonical_name   TEXT,                    -- raw name as Steam returned it
    seen_at_ns       INTEGER NOT NULL,
    schema_version   TEXT NOT NULL DEFAULT 'v1.0'
);


-- ---------------------------------------------------------------------
-- leagues — dimension table.
-- Populated by Phase 1 backfill from observed league_ids. Manually fill
-- `tournament_name` and `prize_tier` for the top leagues so backtests
-- can stratify by tournament regime.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS leagues (
    league_id        TEXT PRIMARY KEY,
    tournament_name  TEXT,
    prize_tier       TEXT,                    -- 'tier_1' | 'tier_2' | ... | NULL
    first_seen_ns    INTEGER,
    last_seen_ns     INTEGER,
    match_count      INTEGER NOT NULL DEFAULT 0,
    schema_version   TEXT NOT NULL DEFAULT 'v1.0'
);


-- ---------------------------------------------------------------------
-- runs — replaces the run_id/code_version/config_hash columns spread
-- across signals.csv / dota_events.csv. One row per bot process start.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS runs (
    run_id           TEXT PRIMARY KEY,
    started_at_ns    INTEGER NOT NULL,
    stopped_at_ns    INTEGER,
    code_version     TEXT,
    config_hash      TEXT,
    mode             TEXT,                    -- 'paper' | 'live' | 'shadow'
    notes            TEXT,
    schema_version   TEXT NOT NULL DEFAULT 'v1.0'
);
