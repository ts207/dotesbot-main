"""PyArrow schemas for the stream tables in data_v2/.

Schema design rules:
1. Faithful to the existing CSV column names so the backfill is a direct
   column-by-column copy with no renames. Net-new fields (UUIDs, partition
   key columns) are added at the end.
2. All timestamps as `received_at_ns` (int64, Unix nanoseconds). The
   string `received_at_utc` is retained for human-readable debugging but
   queries should use the nanosecond column.
3. `match_id`, `lobby_id`, `league_id`, `token_id`, `asset_id` are strings
   (often 30-77 char numerics that overflow int64).
4. Nullable everywhere — historical rows often lack fields added later.
5. `schema_version` column on every row so we can evolve without breaking
   the parquet reader.
"""
from __future__ import annotations

import pyarrow as pa

SCHEMA_VERSION = "v1.0"


# ---------- snapshots ----------
# Source: raw_snapshots.csv (19 cols) + rich_context.csv (290 cols, mostly
# per-player). For v1.0 we keep game-level state only. Per-player rich state
# can become a separate `player_snapshots` table if a use case appears.
SCHEMA_SNAPSHOTS = pa.schema([
    # Identity / time
    pa.field("received_at_utc", pa.string()),
    pa.field("received_at_ns", pa.int64()),
    pa.field("match_id", pa.string()),
    pa.field("lobby_id", pa.string()),
    pa.field("league_id", pa.string()),
    pa.field("server_steam_id", pa.string()),
    # Game state
    pa.field("game_time_sec", pa.int32()),
    pa.field("radiant_lead", pa.int32()),
    pa.field("radiant_score", pa.int32()),
    pa.field("dire_score", pa.int32()),
    pa.field("building_state", pa.int64()),
    pa.field("tower_state", pa.int64()),
    pa.field("roshan_respawn_timer", pa.int32()),
    pa.field("stream_delay_s", pa.int32()),
    pa.field("source_update_age_sec", pa.float64()),
    pa.field("data_source", pa.string()),
    pa.field("spectators", pa.int32()),
    pa.field("game_over", pa.bool_()),
    # Rich context (nullable; absent when source is raw_snapshots only)
    pa.field("series_id", pa.string()),
    pa.field("series_type", pa.int32()),
    pa.field("radiant_team", pa.string()),
    pa.field("dire_team", pa.string()),
    pa.field("radiant_team_id", pa.string()),
    pa.field("dire_team_id", pa.string()),
    pa.field("radiant_net_worth", pa.int32()),
    pa.field("dire_net_worth", pa.int32()),
    pa.field("net_worth_diff", pa.int32()),
    # Partition / versioning
    pa.field("date", pa.string()),
    pa.field("schema_version", pa.string()),
    pa.field("source_file", pa.string()),  # provenance: which CSV/JSONL row came from
])


# ---------- book_ticks ----------
# Source: book_events.csv. One row per Polymarket WS top-of-book update.
SCHEMA_BOOK_TICKS = pa.schema([
    pa.field("received_at_utc", pa.string()),
    pa.field("received_at_ns", pa.int64()),
    pa.field("asset_id", pa.string()),
    pa.field("event_type", pa.string()),       # always "BOOK_TOP" in existing data
    pa.field("source_event_type", pa.string()),  # always "book" in existing data
    pa.field("best_bid", pa.float64()),
    pa.field("best_ask", pa.float64()),
    pa.field("bid_size", pa.float64()),
    pa.field("ask_size", pa.float64()),
    pa.field("mid", pa.float64()),
    pa.field("spread", pa.float64()),
    pa.field("date", pa.string()),
    pa.field("schema_version", pa.string()),
    pa.field("source_file", pa.string()),
])


# ---------- dota_events ----------
# Source: dota_events.csv. event_detector output, before signal_engine consumes it.
SCHEMA_DOTA_EVENTS = pa.schema([
    pa.field("received_at_utc", pa.string()),
    pa.field("received_at_ns", pa.int64()),
    pa.field("run_id", pa.string()),
    pa.field("code_version", pa.string()),
    pa.field("config_hash", pa.string()),
    pa.field("match_id", pa.string()),
    pa.field("lobby_id", pa.string()),
    pa.field("league_id", pa.string()),
    pa.field("mapping_name", pa.string()),
    pa.field("yes_team", pa.string()),
    pa.field("yes_token_id", pa.string()),
    pa.field("event_type", pa.string()),
    pa.field("event_tier", pa.string()),
    pa.field("event_is_primary", pa.bool_()),
    pa.field("event_family", pa.string()),
    pa.field("event_quality", pa.float64()),
    pa.field("event_dedupe_key", pa.string()),
    pa.field("event_schema_version", pa.string()),
    pa.field("snapshot_gap_sec", pa.float64()),
    pa.field("actual_window_sec", pa.float64()),
    pa.field("networth_delta", pa.float64()),
    pa.field("kill_diff_delta", pa.float64()),
    pa.field("total_kills_delta", pa.float64()),
    pa.field("networth_delta_per_30s", pa.float64()),
    pa.field("kill_diff_delta_per_30s", pa.float64()),
    pa.field("source_cadence_quality", pa.string()),
    pa.field("component_event_types", pa.string()),
    pa.field("component_deltas", pa.string()),
    pa.field("component_window_sec", pa.string()),
    pa.field("severity", pa.string()),
    pa.field("game_time_sec", pa.int32()),
    pa.field("radiant_team", pa.string()),
    pa.field("dire_team", pa.string()),
    pa.field("radiant_lead", pa.int32()),
    pa.field("radiant_score", pa.int32()),
    pa.field("dire_score", pa.int32()),
    pa.field("tower_state", pa.int64()),
    pa.field("previous_value", pa.string()),
    pa.field("current_value", pa.string()),
    pa.field("delta", pa.float64()),
    pa.field("window_sec", pa.float64()),
    pa.field("threshold", pa.float64()),
    pa.field("direction", pa.string()),
    pa.field("base_pressure_score", pa.float64()),
    pa.field("fight_pressure_score", pa.float64()),
    pa.field("economic_pressure_score", pa.float64()),
    pa.field("conversion_score", pa.float64()),
    pa.field("event_confidence", pa.float64()),
    pa.field("date", pa.string()),
    pa.field("schema_version", pa.string()),
    pa.field("source_file", pa.string()),
])


# ---------- signals ----------
# Source: signals.csv (73 cols). signal_engine output (one per evaluate_cluster
# call). `signal_id` is a deterministic UUID5 derived from
# (match_id, event_type, received_at_ns) so backfilled rows can be joined to
# markouts/attempts that reference them.
SCHEMA_SIGNALS = pa.schema([
    pa.field("signal_id", pa.string()),     # NEW: uuid5 join key
    pa.field("received_at_utc", pa.string()),
    pa.field("received_at_ns", pa.int64()),
    pa.field("run_id", pa.string()),
    pa.field("code_version", pa.string()),
    pa.field("config_hash", pa.string()),
    # game identity
    pa.field("match_id", pa.string()),
    pa.field("lobby_id", pa.string()),
    pa.field("league_id", pa.string()),
    pa.field("radiant_team", pa.string()),
    pa.field("dire_team", pa.string()),
    pa.field("game_time_sec", pa.int32()),
    pa.field("radiant_lead", pa.int32()),
    pa.field("radiant_score", pa.int32()),
    pa.field("dire_score", pa.int32()),
    # market identity
    pa.field("market_name", pa.string()),
    pa.field("market_type", pa.string()),
    pa.field("yes_team", pa.string()),
    pa.field("yes_token_id", pa.string()),
    pa.field("token_id", pa.string()),
    pa.field("side", pa.string()),
    # event
    pa.field("event_type", pa.string()),
    pa.field("cluster_event_types", pa.string()),
    pa.field("event_direction", pa.string()),
    pa.field("severity", pa.string()),
    pa.field("event_tier", pa.string()),
    pa.field("event_is_primary", pa.bool_()),
    pa.field("event_family", pa.string()),
    pa.field("event_quality", pa.float64()),
    pa.field("event_schema_version", pa.string()),
    pa.field("snapshot_gap_sec", pa.float64()),
    pa.field("actual_window_sec", pa.float64()),
    pa.field("networth_delta", pa.float64()),
    pa.field("kill_diff_delta", pa.float64()),
    pa.field("total_kills_delta", pa.float64()),
    pa.field("networth_delta_per_30s", pa.float64()),
    pa.field("kill_diff_delta_per_30s", pa.float64()),
    pa.field("source_cadence_quality", pa.string()),
    # signal output
    pa.field("lag", pa.float64()),
    pa.field("expected_move", pa.float64()),
    pa.field("fair_price", pa.float64()),
    pa.field("executable_price", pa.float64()),
    pa.field("executable_edge", pa.float64()),
    pa.field("remaining_move", pa.float64()),
    pa.field("fair_source", pa.string()),
    pa.field("market_move_recent", pa.float64()),
    pa.field("price_lookback_sec", pa.float64()),
    pa.field("pregame_move", pa.float64()),
    pa.field("anchor_price", pa.float64()),
    pa.field("current_price", pa.float64()),
    pa.field("bid", pa.float64()),
    pa.field("ask", pa.float64()),
    pa.field("spread", pa.float64()),
    pa.field("ask_size", pa.float64()),
    pa.field("price_quality_score", pa.float64()),
    pa.field("execution_quality_score", pa.float64()),
    pa.field("trade_score", pa.float64()),
    pa.field("target_size_usd", pa.float64()),
    pa.field("size_multiplier", pa.float64()),
    pa.field("phase_mult", pa.float64()),
    pa.field("event_kill_lead", pa.float64()),
    pa.field("decision", pa.string()),
    pa.field("skip_reason", pa.string()),
    pa.field("steam_age_ms", pa.float64()),
    pa.field("estimated_game_time_sec", pa.float64()),
    pa.field("source_update_age_sec", pa.float64()),
    pa.field("stream_delay_s", pa.float64()),
    pa.field("data_source", pa.string()),
    pa.field("book_age_ms", pa.float64()),
    pa.field("book_age_at_signal_ms", pa.float64()),
    pa.field("mapping_confidence", pa.float64()),
    pa.field("mapping_errors", pa.string()),
    pa.field("team_id_match", pa.string()),
    pa.field("market_game_number_match", pa.string()),
    pa.field("duplicate_match_id_error", pa.string()),
    pa.field("slow_model_fair", pa.float64()),
    pa.field("fast_event_adjustment", pa.float64()),
    pa.field("hybrid_fair", pa.float64()),
    pa.field("hybrid_confidence", pa.float64()),
    pa.field("uncertainty_penalty", pa.float64()),
    pa.field("proxy_market_type", pa.string()),
    pa.field("is_game3_match_proxy", pa.bool_()),
    pa.field("series_score_yes", pa.float64()),
    pa.field("series_score_no", pa.float64()),
    pa.field("current_game_number", pa.int32()),
    pa.field("series_type", pa.string()),
    pa.field("structure_uncertainty_penalty", pa.float64()),
    pa.field("date", pa.string()),
    pa.field("schema_version", pa.string()),
    pa.field("source_file", pa.string()),
])


# ---------- trade_attempts ----------
# Union of: live_attempts.csv and paper_trades.csv.
# `trader_kind` distinguishes the writer; `signal_id` joins back to signals.
SCHEMA_TRADE_ATTEMPTS = pa.schema([
    pa.field("attempt_id", pa.string()),     # NEW: uuid5(match, token, ts)
    pa.field("signal_id", pa.string()),      # NEW: foreign key to signals
    pa.field("trader_kind", pa.string()),
    pa.field("received_at_utc", pa.string()),
    pa.field("received_at_ns", pa.int64()),
    pa.field("phase", pa.string()),
    pa.field("event_type", pa.string()),
    pa.field("event_direction", pa.string()),
    pa.field("token_id", pa.string()),
    pa.field("side", pa.string()),
    pa.field("market_name", pa.string()),
    pa.field("match_id", pa.string()),
    pa.field("game_time_sec", pa.int32()),
    pa.field("fair_price", pa.float64()),
    pa.field("best_ask", pa.float64()),
    pa.field("price_cap", pa.float64()),
    pa.field("edge", pa.float64()),
    pa.field("lag", pa.float64()),
    pa.field("spread", pa.float64()),
    pa.field("event_quality", pa.float64()),
    pa.field("event_schema_version", pa.string()),
    pa.field("source_cadence_quality", pa.string()),
    pa.field("book_age_ms", pa.float64()),
    pa.field("steam_age_ms", pa.float64()),
    pa.field("order_type", pa.string()),
    pa.field("submitted_size_usd", pa.float64()),
    pa.field("filled_size_usd", pa.float64()),
    pa.field("avg_fill_price", pa.float64()),
    pa.field("order_status", pa.string()),
    pa.field("reason_if_rejected", pa.string()),
    pa.field("markout_3s", pa.float64()),
    pa.field("markout_10s", pa.float64()),
    pa.field("markout_30s", pa.float64()),
    pa.field("raw_response_json", pa.string()),
    pa.field("date", pa.string()),
    pa.field("schema_version", pa.string()),
    pa.field("source_file", pa.string()),
])


# ---------- exits ----------
SCHEMA_EXITS = pa.schema([
    pa.field("received_at_utc", pa.string()),
    pa.field("received_at_ns", pa.int64()),
    pa.field("position_id", pa.string()),
    pa.field("token_id", pa.string()),
    pa.field("match_id", pa.string()),
    pa.field("reason", pa.string()),
    pa.field("shares_requested", pa.float64()),
    pa.field("shares_filled", pa.float64()),
    pa.field("best_bid", pa.float64()),
    pa.field("price_posted", pa.float64()),
    pa.field("order_status", pa.string()),
    pa.field("reason_if_rejected", pa.string()),
    pa.field("submit_start_ns", pa.int64()),
    pa.field("response_received_ns", pa.int64()),
    pa.field("submit_latency_ms", pa.float64()),
    pa.field("raw_response_json", pa.string()),
    pa.field("date", pa.string()),
    pa.field("schema_version", pa.string()),
    pa.field("source_file", pa.string()),
])


# ---------- markouts ----------
# Union of markouts.csv (lean) and signal_markouts.csv (full). One row per
# (signal_id, horizon_sec). Backfill expands the wide signal_markouts.csv
# columns (markout_3s/10s/30s) into multiple rows.
SCHEMA_MARKOUTS = pa.schema([
    pa.field("signal_id", pa.string()),
    pa.field("signal_received_at_ns", pa.int64()),
    pa.field("computed_at_ns", pa.int64()),
    pa.field("match_id", pa.string()),
    pa.field("market_name", pa.string()),
    pa.field("token_id", pa.string()),
    pa.field("event_type", pa.string()),
    pa.field("horizon_sec", pa.int32()),
    pa.field("reference_price", pa.float64()),
    pa.field("reference_bid", pa.float64()),
    pa.field("reference_ask", pa.float64()),
    pa.field("markout_price_delta", pa.float64()),
    pa.field("edge_after", pa.float64()),
    pa.field("decision_at_signal", pa.string()),
    pa.field("side", pa.string()),
    pa.field("date", pa.string()),
    pa.field("schema_version", pa.string()),
    pa.field("source_file", pa.string()),
])


# ---------- source_delay ----------
# Source: source_delay.csv. Per-snapshot tracking of Steam GetRealtimeStats
# vs GetTopLiveGame lag.
SCHEMA_SOURCE_DELAY = pa.schema([
    pa.field("received_at_utc", pa.string()),
    pa.field("received_at_ns", pa.int64()),
    pa.field("match_id", pa.string()),
    pa.field("lobby_id", pa.string()),
    pa.field("league_id", pa.string()),
    pa.field("realtime_game_time_sec", pa.int32()),
    pa.field("toplive_game_time_sec", pa.int32()),
    pa.field("game_time_lag_sec", pa.float64()),
    pa.field("realtime_stats_age_sec", pa.float64()),
    pa.field("realtime_context_status", pa.string()),
    pa.field("date", pa.string()),
    pa.field("schema_version", pa.string()),
    pa.field("source_file", pa.string()),
])


# ---------- latency ----------
# Source: latency.csv. Wide-format diagnostic per-signal latency record.
# Keeping all 60 source columns plus the canonical received_at_* pair.
SCHEMA_LATENCY = pa.schema([
    pa.field("received_at_utc", pa.string()),
    pa.field("received_at_ns", pa.int64()),
    pa.field("run_id", pa.string()),
    pa.field("code_version", pa.string()),
    pa.field("config_hash", pa.string()),
    pa.field("match_id", pa.string()),
    pa.field("market_name", pa.string()),
    pa.field("event_type", pa.string()),
    pa.field("cluster_event_types", pa.string()),
    pa.field("event_direction", pa.string()),
    pa.field("game_time_sec", pa.int32()),
    pa.field("data_source", pa.string()),
    pa.field("steam_received_at_ns", pa.int64()),
    pa.field("steam_source_update_age_sec", pa.float64()),
    pa.field("stream_delay_s", pa.float64()),
    pa.field("event_detected_ns", pa.int64()),
    pa.field("signal_eval_start_ns", pa.int64()),
    pa.field("signal_evaluated_ns", pa.int64()),
    pa.field("event_detection_latency_ms", pa.float64()),
    pa.field("signal_eval_latency_ms", pa.float64()),
    pa.field("token_id", pa.string()),
    pa.field("side", pa.string()),
    pa.field("book_received_at_ns", pa.int64()),
    pa.field("book_age_at_signal_ms", pa.float64()),
    pa.field("best_bid", pa.float64()),
    pa.field("best_ask", pa.float64()),
    pa.field("spread", pa.float64()),
    pa.field("ask_size", pa.float64()),
    pa.field("decision", pa.string()),
    pa.field("skip_reason", pa.string()),
    pa.field("fair_price", pa.float64()),
    pa.field("executable_price", pa.float64()),
    pa.field("executable_edge", pa.float64()),
    pa.field("remaining_move", pa.float64()),
    pa.field("fair_source", pa.string()),
    pa.field("required_edge", pa.float64()),
    pa.field("lag", pa.float64()),
    pa.field("paper_delay_ms", pa.float64()),
    pa.field("paper_attempt_ns", pa.int64()),
    pa.field("paper_fill_ns", pa.int64()),
    pa.field("paper_entry_result", pa.string()),
    pa.field("paper_fill_price", pa.float64()),
    pa.field("paper_entry_latency_ms", pa.float64()),
    pa.field("live_submit_start_ns", pa.int64()),
    pa.field("live_response_received_ns", pa.int64()),
    pa.field("live_submit_latency_ms", pa.float64()),
    pa.field("live_order_status", pa.string()),
    pa.field("live_reject_reason", pa.string()),
    pa.field("live_submitted_size_usd", pa.float64()),
    pa.field("live_filled_size_usd", pa.float64()),
    pa.field("live_avg_fill_price", pa.float64()),
    pa.field("mapping_confidence", pa.float64()),
    pa.field("mapping_errors", pa.string()),
    pa.field("team_id_match", pa.string()),
    pa.field("market_game_number_match", pa.string()),
    pa.field("duplicate_match_id_error", pa.string()),
    pa.field("slow_model_fair", pa.float64()),
    pa.field("fast_event_adjustment", pa.float64()),
    pa.field("hybrid_fair", pa.float64()),
    pa.field("hybrid_confidence", pa.float64()),
    pa.field("uncertainty_penalty", pa.float64()),
    pa.field("date", pa.string()),
    pa.field("schema_version", pa.string()),
    pa.field("source_file", pa.string()),
])


SCHEMA_VALUE_ATTEMPTS = pa.schema([
    pa.field("received_at_utc", pa.string()),
    pa.field("received_at_ns", pa.int64()),
    pa.field("signal_id", pa.string()),
    pa.field("match_id", pa.string()),
    pa.field("would_trade", pa.bool_()),
    pa.field("reject_reason", pa.string()),
    pa.field("direction", pa.string()),
    pa.field("side", pa.string()),
    pa.field("token_id", pa.string()),
    pa.field("fair_price", pa.float64()),
    pa.field("ask", pa.float64()),
    pa.field("edge", pa.float64()),
    pa.field("lead", pa.int32()),
    pa.field("game_time_sec", pa.int32()),
    pa.field("elo_diff", pa.float64()),
    pa.field("book_age_ms", pa.float64()),
    pa.field("sized_usd", pa.float64()),
    pa.field("date", pa.string()),
    pa.field("schema_version", pa.string()),
    pa.field("source_file", pa.string()),
])


SCHEMA_STRATEGY_ALLOCATOR = pa.schema([
    pa.field("received_at_utc", pa.string()),
    pa.field("received_at_ns", pa.int64()),
    pa.field("match_id", pa.string()),
    pa.field("token_id", pa.string()),
    pa.field("winner_strategy", pa.string()),
    pa.field("winner_edge", pa.float64()),
    pa.field("winner_fair", pa.float64()),
    pa.field("winner_game_time_sec", pa.int32()),
    pa.field("winner_direction", pa.string()),
    pa.field("winner_event_subtype", pa.string()),
    pa.field("winner_is_reversal", pa.bool_()),
    pa.field("winner_edge_type", pa.string()),
    pa.field("winner_target_horizon", pa.string()),
    pa.field("winner_expected_hold_sec", pa.float64()),
    pa.field("winner_entry_trigger", pa.string()),
    pa.field("winner_exit_trigger", pa.string()),
    pa.field("winner_primary_metric", pa.string()),
    pa.field("candidate_count", pa.int32()),
    pa.field("blocked_count", pa.int32()),
    pa.field("blocked_strategies", pa.string()),
    pa.field("blocked_edges", pa.string()),
    pa.field("blocked_fairs", pa.string()),
    pa.field("blocked_edge_types", pa.string()),
    pa.field("blocked_target_horizons", pa.string()),
    pa.field("blocked_expected_hold_secs", pa.string()),
    pa.field("block_reason", pa.string()),
    pa.field("counterfactual_note", pa.string()),
    pa.field("date", pa.string()),
    pa.field("schema_version", pa.string()),
    pa.field("source_file", pa.string()),
])


ALL_SCHEMAS = {
    "snapshots": SCHEMA_SNAPSHOTS,
    "book_ticks": SCHEMA_BOOK_TICKS,
    "dota_events": SCHEMA_DOTA_EVENTS,
    "signals": SCHEMA_SIGNALS,
    "trade_attempts": SCHEMA_TRADE_ATTEMPTS,
    "exits": SCHEMA_EXITS,
    "markouts": SCHEMA_MARKOUTS,
    "value_attempts": SCHEMA_VALUE_ATTEMPTS,
    "source_delay": SCHEMA_SOURCE_DELAY,
    "latency": SCHEMA_LATENCY,
    "strategy_allocator": SCHEMA_STRATEGY_ALLOCATOR,
}
