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
