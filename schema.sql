CREATE TABLE IF NOT EXISTS live_positions (
  position_id TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  token_id TEXT NOT NULL,
  match_id TEXT NOT NULL,
  side TEXT,
  strategy_kind TEXT,
  strategy_family TEXT,
  raw_json TEXT NOT NULL,
  updated_at_ns INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS live_orders (
  order_id TEXT PRIMARY KEY,
  position_id TEXT,
  token_id TEXT,
  match_id TEXT,
  state TEXT,
  raw_json TEXT,
  updated_at_ns INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS live_reconciliations (
  reconciliation_id TEXT PRIMARY KEY,
  checked_tokens INTEGER,
  closed_stale INTEGER,
  reopened_missing INTEGER,
  adjusted_existing INTEGER,
  active_after INTEGER,
  raw_json TEXT,
  created_at_ns INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_positions (
  position_id TEXT PRIMARY KEY,
  state TEXT,
  token_id TEXT,
  match_id TEXT,
  strategy_kind TEXT,
  raw_json TEXT,
  updated_at_ns INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_orders (
  order_id TEXT PRIMARY KEY,
  token_id TEXT,
  match_id TEXT,
  state TEXT,
  raw_json TEXT,
  updated_at_ns INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_signals (
  signal_id TEXT PRIMARY KEY,
  match_id TEXT,
  strategy_kind TEXT,
  token_id TEXT,
  raw_json TEXT,
  created_at_ns INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS allocation_decisions (
  decision_id TEXT PRIMARY KEY,
  match_id TEXT,
  strategy_kind TEXT,
  raw_json TEXT,
  created_at_ns INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_decisions (
  policy_id TEXT PRIMARY KEY,
  match_id TEXT,
  token_id TEXT,
  allowed INTEGER,
  reason TEXT,
  policy_version TEXT,
  raw_json TEXT,
  created_at_ns INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS mapping_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  market_id TEXT,
  condition_id TEXT,
  dota_match_id TEXT,
  mapping_state TEXT,
  raw_json TEXT,
  created_at_ns INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS feed_health (
  health_id TEXT PRIMARY KEY,
  source TEXT,
  status TEXT,
  raw_json TEXT,
  created_at_ns INTEGER NOT NULL
);

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
