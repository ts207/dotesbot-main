-- Migration 002: Align StateStore schema with StorageV2
-- =======================================================
--
-- NOTE: This file is a REFERENCE / RUNBOOK document, not a standalone
-- executable migration.  The runtime migration is handled automatically
-- by StorageV2.init_db() in storage_v2.py.
--
-- If you need to apply this manually to an existing deployment, follow
-- the steps in "Manual operator steps" below rather than running this
-- file verbatim — a naive CREATE TABLE will silently no-op against an
-- existing date-only daily_budgets table and leave the old schema intact.
--
-- -----------------------------------------------------------------------
-- Automatic runtime migration (canonical path)
-- -----------------------------------------------------------------------
-- StorageV2.init_db() detects the four possible DB states on every startup:
--
--   State 1 – Fresh DB: runs CREATE IF NOT EXISTS for all tables.
--   State 2 – Old date-only daily_budgets (no mode column):
--              renames to daily_budgets_legacy_date_only, creates the new
--              (date_str, mode) table, copies old rows under mode='legacy'.
--   State 3 – Already-migrated (mode column present): no-op.
--   State 4 – Partial/interrupted migration (legacy backup table exists):
--              idempotently replays the copy step (INSERT OR IGNORE),
--              creating daily_budgets first if it is still absent.
--
-- -----------------------------------------------------------------------
-- Manual operator steps (only if running outside the Python runtime)
-- -----------------------------------------------------------------------
-- Step 1: Back up the database before proceeding.
--
-- Step 2: Check current schema:
--   PRAGMA table_info(daily_budgets);
--   -- If 'mode' column is already present, you are on State 3 – skip all.
--
-- Step 3: Rename the old table:
--   ALTER TABLE daily_budgets RENAME TO daily_budgets_legacy_date_only;
--
-- Step 4: Create the new mode-keyed table:

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

-- Step 5: Copy legacy rows under the 'legacy' sentinel mode
--   (INSERT OR IGNORE is safe to replay if interrupted):
INSERT OR IGNORE INTO daily_budgets (
    date_str, mode, total_submitted_usd, total_filled_usd,
    open_positions, daily_realized_pnl_usd, submitted_match_sides,
    submitted_match_usd, submitted_family_usd, updated_at_ns
)
SELECT
    date_str, 'legacy', total_submitted_usd, total_filled_usd,
    open_positions, daily_realized_pnl_usd, submitted_match_sides,
    submitted_match_usd, submitted_family_usd, updated_at_ns
FROM daily_budgets_legacy_date_only;

-- -----------------------------------------------------------------------
-- Other StorageV2 tables (safe CREATE IF NOT EXISTS — idempotent)
-- -----------------------------------------------------------------------

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
