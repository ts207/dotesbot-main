# Dotesbot Codebase Map

This document provides a map of the dotesbot codebase, organized by function.

### 1. Core Bot Loop
*   `main.py`: Thin entry point — imports `BotRuntime`, calls `BotRuntime(cfg).run()`
*   `supervisor.py`: Watchdog that launches + auto-restarts `main.py` and `auto_series_binder.py`.
*   `runtime/bot_runtime.py`: **The actual bot loop** — main async loop that polls Steam, refreshes books, runs engines, manages exits.
*   `runtime/*.py`: Sub-runtimes for feed, execution, mapping, strategies.

### 2. Strategies
*   `value_engine.py`: **Value strategy** — backs NW leader when model fair price > book price.
*   `decisive_swing_engine.py`: **Decisive-swing ML sniper** — buys BO3 moneyline when a map's NW lead crosses a threshold.
*   `event_triggered_value_engine.py`: **Event-triggered value** — fires on actual Dota events when fair diverges from book.
*   `strategy_collection.py` & `strategy_allocator.py`: Collects candidates and handles priority-based allocation.
*   `strategy_execution.py`: Dispatches allocation winners to live executors or paper traders.
*   `execution_policy.py`: Unified gate/policy evaluation for all strategies.

### 3. Win Probability Model
*   `winprob.py`: Runtime calibrated win-probability model `fair(lead, game_time_sec, elo_diff, lead_slope, draft_h2h)`.
*   `fit_winprob.py`: Fits the logistic winprob model.
*   `fair_value.py`: Wrapper that calls winprob and returns `FairValueResult`.

### 4. Market/Exchange Interaction
*   `live_executor.py`: **The order execution engine** (FAK orders, caps, budget rails).
*   `book_refresh.py`: REST-based order book snapshot from Polymarket CLOB API.
*   `poly_ws.py`: Polymarket WebSocket client (in-memory top-of-book store).
*   `poly_gamma.py`: Polymarket Gamma API client.

### 5. Data Feeds
*   `steam_client.py`: Steam Web API client (GetTopLiveGame + GetLiveLeagueGames + GetRealtimeStats).
*   `liveleague_features.py`: Extracts rich features from GetLiveLeagueGames.
*   `realtime_enrichment.py`: Attaches delayed GetRealtimeStats context without overwriting fast TopLive data.
*   `hybrid_nowcast.py`: Merges slow ML model fair with fast event adjustments.

### 6. State Management
*   `storage.py`: Mega CSV logger module.
*   `storage_v2.py` & `state_store.py`: SQLite backend replacing JSON/CSV for bot state.
*   `live_position_store.py`: Manages active position lifecycle.
*   `live_state.py`: Persists daily risk budget state.

### 7. Market Mapping/Binding
*   `auto_series_binder.py`: **Auto-discovery + binding** loop (maps Polymarket markets → live Steam matches).
*   `mapping.py` & `mapping_validator.py`: Loads and validates `markets.yaml`.
*   `mapping_audit.py` & `mapping_quarantine.py`: Audits mapping health, auto-quarantines issues.
*   `discover_markets.py`: Discovers new Dota markets from Polymarket API.
*   `sync_markets.py`: Full sync orchestration (discover → auto-bind → audit → quarantine).

### 8. Position/Exit Management
*   `positions.py`: Position marking/valuation at book prices, P&L calculation.
*   `live_exit_engine.py`: Exit decision logic (game_over, catastrophe-salvage, max-hold).
*   `exit_policy.py`: Unified exit gate for value-family positions.
*   `live_reconciliation.py`: Startup reconcile of on-chain balances vs stored positions.

### 9. Monitoring/Ops
*   `monitor.py`: Health + risk monitor (snapshots NAV, checks heartbeat/drawdown).
*   `cockpit.py`: **Manual trading TUI**.
*   `dashboard.py` & `dashboard_live.py`: Web dashboard and CLI dashboard.
*   `settlement_shadow.py`: Shadow trade ledger for hold-to-settle outcomes.

### 10. Event Detection
*   `actual_dota_event_detector.py`: Detects primitive events (kill score changes, tower destroyed).
*   `event_detector.py`: Legacy event detector for compound events.
*   `event_taxonomy.py`: Event tier classification.
*   `derived_game_state.py`: Computes phase-adjusted NW lead, structure advantage.

### 11. Configuration
*   `config.py`: Central config — loads `.env`, sets all constants and knobs.
*   `runtime_config.py`: Typed config dataclasses.

### Directories
*   `scripts/`: Backfill, sweep, extraction, and validation tools.
*   `ops/`: Pre-launch checks, sync tools, systemd services, API key management.
*   `unified_storage/`: Writers and schemas for `data_v2/` Parquet storage.
*   `strategies/`: YAML strategy contracts.
*   `tests/`: Comprehensive test suite.
