# AGENTS.md

Operational and development guide for agents working on this repository.

This project is a Python Dota 2 / Polymarket trading bot. It ingests live Dota
state from Steam, maps live games to Polymarket markets, computes strategy
signals, gates them through execution policy and risk rails, and can route
orders to the Polymarket CLOB when live trading is explicitly enabled.

Treat this repository as a trading system first and a research codebase second:
preserve safety gates, mapping correctness, durable state, and audit logs.

## First Rules

- Do not run `main.py` directly for normal operation. Use `python3 supervisor.py`.
- Do not enable real live trading from repository defaults. Real mode must have
  explicit safe `.env` values and valid Polymarket credentials.
- Do not commit real secrets, wallet keys, API keys, live position state, or
  ad hoc runtime logs.
- Do not bypass `execution_policy.py`, mapping validation, disk guard, or live
  reconciliation to make a strategy "work".
- Prefer focused tests around the changed module, then run the full suite when a
  change touches shared runtime, policy, mapping, storage, or execution paths.
- Keep changes compatible with Python 3.12; CI runs `python -m pytest tests/`.

## Repository Map

Core runtime:

- `main.py`: thin entry point that loads typed config and runs `BotRuntime`.
- `runtime/bot_runtime.py`: actual async bot loop. It polls Steam, refreshes
  mappings, listens to books, collects strategy candidates, allocates winners,
  executes entries, and manages exits.
- `supervisor.py`: watchdog process manager. Starts and restarts the bot,
  auto binder, settlement shadow process, and monitor based on process death or
  stale heartbeat files.
- `runtime/*.py`: smaller runtime helpers for feed, execution, mapping, markout,
  and strategy work. Some legacy code still lives in `runtime/bot_runtime.py`.

Strategies:

- `value_engine.py`: structural value strategy. Backs the net-worth leader when
  model fair exceeds the executable ask under value-specific gates.
- `event_triggered_value_engine.py`: actual-Dota-event value strategy. Uses
  primitive Dota events plus fair value movement to create continuation or
  reversal value signals.
- `decisive_swing_engine.py`: BO3 match-winner sniper. Uses decisive map lead to
  buy slow-to-reprice series markets, then exits on map-end convergence.
- `strategy_collection.py`: converts engine results into `StrategyCandidate`
  objects and logs rejects/signals.
- `strategy_allocator.py`: pure allocation layer. Chooses one winning strategy
  per token and records counterfactual blocked candidates.
- `strategy_execution.py`: executes allocator winners through paper or live paths.
- `strategy_registry.py` and `strategies/*.yaml`: strategy identity/contracts
  used to stamp metadata such as edge type, horizon, triggers, and disable rules.

Market and exchange integration:

- `mapping.py`: loads `markets.yaml` plus `logs/runtime_markets.yaml` overlay and
  returns only valid, non-quarantined mappings.
- `mapping_validator.py`: schema, identity, duplicate, team, league, and series
  validation.
- `mapping_audit.py` and `mapping_quarantine.py`: audit mappings and quarantine
  critical failures.
- `sync_markets.py`: binds known markets to live Steam games.
- `auto_series_binder.py`: supervised market discovery/binding loop.
- `discover_markets.py`: Polymarket Dota market discovery.
- `book_refresh.py`: REST order-book snapshot.
- `poly_ws.py`: Polymarket WebSocket top-of-book store.
- `poly_gamma.py`: Polymarket Gamma API client.
- `live_executor.py`: live and dry-live order attempt engine.
- `live_reconciliation.py`: startup/runtime reconciliation of exchange balances
  against stored live positions.

Dota data and modeling:

- `steam_client.py`: Steam Web API client for TopLive, LiveLeague, and realtime
  stats.
- `liveleague_features.py`: rich LiveLeague feature extraction.
- `realtime_enrichment.py`: delayed realtime context attachment. This must not
  overwrite faster TopLive fields.
- `actual_dota_event_detector.py`: primitive Dota event detection.
- `event_detector.py`: legacy compound event detector, mostly diagnostic now.
- `derived_game_state.py`: derived net-worth, structure, and game-state flags.
- `fair_value.py` and `winprob.py`: calibrated side fair value.
- `series_model.py`: BO3/BO5 series probability logic.

State, logs, and data:

- `storage.py`: CSV logger layer and state mirroring hooks.
- `storage_v2.py`: SQLite state backend for active/closed positions and budgets.
- `state_store.py` plus `schema.sql`: SQLite mirror for live orders, policy
  decisions, strategy signals, allocation decisions, mappings, and health.
- `live_position_store.py`: active live position lifecycle on top of `StorageV2`.
- `paper_trader.py`: paper position lifecycle and paper exits.
- `unified_storage/`: `data_v2/` Parquet schemas, writers, and operational DB
  helpers.
- `logs/`: runtime output and state. Treat as mutable runtime data, not source.
- `data/`, `data_v2/`, `reports/`: research/backfill outputs and datasets.

Ops and dashboards:

- `monitor.py`: health/risk monitoring loop.
- `settlement_shadow.py`: settlement accounting shadow loop.
- `dashboard.py`, `dashboard_live.py`, `dashboard_assets/`: web/CLI dashboards.
- `cockpit.py`: manual trading TUI.
- `ops/`: systemd units, preflight checks, balance/key scripts, manual order
  helpers, Telegram ops, and live maintenance scripts.
- `scripts/`: research, backfill, validation, stress, and dataset tools.

## Runtime Process Model

Normal operation is:

```bash
python3 supervisor.py
```

`supervisor.py` manages:

- `bot`: `main.py`, heartbeat `logs/heartbeat`, log `logs/stdout.log`.
- `binder`: `auto_series_binder.py --loop`, heartbeat
  `logs/binder_heartbeat`, log `logs/binder.log`.
- `shadow`: `settlement_shadow.py --loop`, heartbeat
  `logs/shadow_heartbeat`, log `logs/settlement_shadow.log`.
- `monitor`: `monitor.py --loop`, heartbeat `logs/monitor_heartbeat`, log
  `logs/monitor.log`.

The supervisor restarts a child when it exits or when its heartbeat is stale.
`runtime/bot_runtime.py` writes startup and loop heartbeats so slow live startup
does not look like a hang.

For local module debugging, it is fine to run narrow scripts or tests. For an
end-to-end bot session, use the supervisor so binder, monitor, and settlement
shadow stay in sync.

## Configuration

Configuration is split between:

- `runtime_config.py`: typed, tracked config for high-risk runtime settings.
- `config.py`: legacy module constants, many now derived from `RUNTIME_CONFIG`.
- `.env.example`: safe template and documentation of knobs.
- `docs/effective_config.md`: details of source tracking and live-mode checks.

Useful config command:

```bash
python check_config.py
```

Important runtime modes:

- `LIVE_TRADING=false` and `ENABLE_REAL_LIVE_TRADING=false`: normal paper mode.
- `LIVE_TRADING=true` and `ENABLE_REAL_LIVE_TRADING=false`: dry-live guarded
  executor simulation.
- `ENABLE_REAL_LIVE_TRADING=true`: real CLOB order path. This fails closed unless
  required live settings are explicitly supplied and pass real-live safety checks.

Required explicit settings for real live mode include:

- `MAX_TOTAL_LIVE_USD`
- `MAX_TRADE_USD`
- `MAX_OPEN_POSITIONS`
- `MAX_DAILY_DRAWDOWN_USD`
- `MAX_STEAM_AGE_MS`
- `MAX_SOURCE_UPDATE_AGE_SEC`
- `MAX_BOOK_AGE_MS`
- `MAX_SPREAD`
- `MIN_ASK_SIZE_USD`
- `MIN_LAG`
- `MIN_EXECUTABLE_EDGE`

Paper modes:

- `PAPER_MODE=research`: allows counterfactual paper entries and labels live
  rejections.
- `PAPER_MODE=live_parity`: rejects paper entries that would fail live gates.
- `PAPER_MODE=shadow_live`: only enters paper when the dry-live policy would
  submit.

Tests override important environment defaults in `tests/conftest.py`. Do not
assume a local `.env` setting is active during tests.

## Live-Trading Safety

The live path is deliberately layered. Keep these layers intact:

1. Strategy engine gates reject bad data, bad mappings, stale books, bad prices,
   and unsupported market types.
2. `execution_policy.py` applies common live and live-parity paper gates:
   mapping validity, supported market type, TopLive freshness, source freshness,
   book freshness, spread, ask size, max fill, event allowlists, cadence schema,
   event quality, duplicate match entries, total exposure, drawdown, and family
   caps.
3. `live_executor.py` applies order sizing, balance, disk guard, budget
   accounting, order-type behavior, and CLOB submission handling.
4. `live_position_store.py` persists active live positions in SQLite.
5. `live_reconciliation.py` cancels stale orders on startup and reconciles local
   position state against exchange balances.
6. `live_exit_engine.py` / `exit_policy.py` decide exits for value, event value,
   decisive swing, and legacy event positions.

Never add a new live entry path that skips `evaluate_policy()` or equivalent
policy metadata. If a new strategy has a special exception, encode it explicitly
in `execution_policy.py` and test it.

The disk guard (`disk_guard.py`) can halt new orders while allowing read-only
monitoring/logging. Low-disk order rejection is expected behavior.

## Strategy Flow

The current active strategy path in `runtime/bot_runtime.py` is:

1. Build per-game/per-mapping context from TopLive, realtime enrichment, mapping
   validation, actual events, and current books.
2. Call `collect_strategy_candidates(...)`.
3. Allocate with `allocate_candidates(...)`.
4. Log allocation decisions with `AllocatorLogger`.
5. Execute winners with `execute_allocation_decisions(...)`.

Allocator priority is:

1. `EVENT_CONTINUATION_EDGE`
2. `VALUE_EDGE`
3. `EVENT_REVERSAL_EDGE`
4. `DSWING`

Allocation is per token. If a token is already entered or pending, all candidates
for that token are blocked as `already_entered`. If multiple candidates target a
free token, the highest-priority candidate wins and the rest are logged as
preempted counterfactuals.

Legacy compound event detection still exists, but active legacy event entries are
disabled by default:

- `ENABLE_LEGACY_EVENT_DIAGNOSTICS = False`
- `ENABLE_LEGACY_EVENT_ENTRIES = False`

Do not build new behavior on the legacy event entry path unless the task
explicitly asks for it.

## Strategy Implementation Pattern

When adding or changing a strategy:

- Return structured signal and reject objects. Rejections should carry enough
  fields for CSV logs and outcome analysis.
- Validate TopLive state with `gettoplive_state.validate_top_live_state()` when
  a strategy depends on live Dota facts.
- Require `game.get("data_source") == "top_live"` for tradeable signals unless
  there is a documented and tested reason not to.
- Check `game_over` before entering.
- Validate market type. Single-game fair values are correct for `MAP_WINNER`;
  `MATCH_WINNER` needs series logic or a game-3 proxy.
- Resolve `YES`/`NO` through `steam_side_mapping` and mapping team identity, not
  ad hoc assumptions.
- Check fresh books and executable ask before computing tradeability.
- Stamp strategy metadata from `strategy_registry.py` / `strategies/*.yaml`.
- Run policy evaluation and include `would_pass_live`, `live_skip_reason`,
  `policy_allowed`, `policy_reason`, `policy_version`, and `risk_tags`.
- Add collection, allocation, execution, logging, and tests together.

Important existing strategy behaviors:

- `VALUE_EDGE` is a hold-to-settle/value thesis strategy with optional
  confirmation via `_value_confirmation_passes`.
- `EVENT_CONTINUATION_EDGE` and `EVENT_REVERSAL_EDGE` use actual primitive
  events plus fair-value deltas. Reversal active exits are disabled by default
  unless `EVENT_REVERSAL_ACTIVE_EXITS_ENABLED=true`.
- `DSWING` is a match-winner strategy for decisive map leads. It exits on
  map-end convergence, not necessarily series settlement.

## Mapping Rules

Mapping mistakes can route trades to the wrong side. Be conservative.

Mappings come from:

- `markets.yaml`: base known/discovered markets.
- `logs/runtime_markets.yaml`: runtime overlay containing mutable fields and
  newly discovered runtime markets.

`mapping.load_valid_mappings()`:

- loads base mappings;
- applies the runtime overlay for the default markets file;
- rejects quarantined entries;
- validates required fields, token IDs, market type, confidence, duplicates,
  placeholders, team identity, league/series fields, and series model state.

Valid active mappings require:

- `market_type` in `MAP_WINNER` or `MATCH_WINNER`;
- `confidence == 1.0`;
- non-placeholder `yes_token_id`, `no_token_id`, and `dota_match_id`;
- distinct YES/NO tokens and teams;
- no active quarantine;
- no invalid duplicate active match binding, except permitted MAP/MATCH winner
  coexistence for the same live match.

For mapping edits:

- Prefer scripts (`discover_markets.py`, `sync_markets.py`,
  `auto_series_binder.py`) over hand editing large YAML sections.
- If hand editing, run focused mapping tests and a mapping audit.
- Preserve `steam_side_mapping`, `steam_radiant_team`, and `steam_dire_team`
  when they explain orientation.
- Use `yes_team_aliases` / `no_team_aliases` for known Steam naming differences
  instead of weakening global matching rules.
- Do not unquarantine a mapping without understanding the quarantine reason.

Useful mapping commands:

```bash
python mapping_audit.py
python sync_markets.py
python discover_markets.py --help
python auto_series_binder.py --loop
```

## Storage and Data

Current state is transitional and intentionally redundant.

Operational source behavior:

- Paper positions are stored through `StorageV2` in `logs/state_v2.sqlite`.
- Live positions are stored through `LivePositionStore`, backed by `StorageV2`.
- `StateStore` mirrors live positions, live orders, policy decisions, strategy
  signals, allocation decisions, and mapping snapshots into `logs/state.sqlite`.
- CSV logs remain important for analysis, dashboards, markouts, and backfills.
- `data_v2/` Parquet schemas preserve CSV column names and add versioned,
  partitioned stream data.

Do not casually change CSV column names or signal metadata names. Many scripts
and tests consume these logs by column.

When changing persistence:

- Keep reads backward-compatible with existing rows.
- Use idempotent migrations.
- Handle partially migrated SQLite states.
- Prefer atomic writes for JSON/YAML/log artifacts that are rewritten in place.
- Add tests with `tmp_path` and monkeypatched DB paths rather than touching real
  `logs/` state.

## Exits and Position Lifecycle

Paper positions:

- `PaperTrader.enter()` fills at best ask, not mid.
- Paper exits sell at best bid.
- `PAPER_MODE` controls whether live-gate failures can become paper entries.
- Opposing token and same-token open positions are blocked.
- Per-match paper exposure is capped by `MAX_OPEN_USD_PER_MATCH`.

Live positions:

- Active states are `OPEN`, `PARTIALLY_EXITED`, `PENDING_ENTRY`,
  `PENDING_EXIT_GTC`, and `EXITING`.
- Startup cancels all open CLOB orders in real live mode and runs reconciliation.
- Pending entries and GTC exits are polled and cleaned up by the runtime.
- Value-family positions use thesis/fair invalidation and catastrophe salvage
  logic; DSWING uses map-end convergence.

When changing exits, test both paper and live policy paths if the behavior
touches shared strategy metadata.

## Logs, Dashboards, and Ops

Common log files:

- `logs/stdout.log`: bot process output under supervisor.
- `logs/binder.log`: binder output.
- `logs/supervisor.log`: supervisor lifecycle.
- `logs/bot.log`: rotating application log.
- `logs/heartbeat`, `logs/binder_heartbeat`, `logs/shadow_heartbeat`,
  `logs/monitor_heartbeat`: watchdog liveness.
- `logs/live_attempts.csv`, `logs/paper_attempts.csv`,
  `logs/strategy_signals.csv`, `logs/strategy_allocator.csv`,
  `logs/live_exits.csv`: execution and strategy audit logs.
- `logs/raw_snapshots.csv`, `logs/book_events.csv`, `logs/actual_dota_events.csv`
  and related CSVs: feed and signal research logs.

Ops commands:

```bash
python ops/preflight.py
python ops/check_api_keys.py
python ops/check_balance.py
python ops/check_active_positions.py
python monitor.py
python dashboard.py
python dashboard_live.py
```

Systemd files live in `ops/`. See `ops/README.md` before changing service
behavior.

Telegram operations are in `ops/telegram_ops.py` and require
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

## Development Setup

Install dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Live CLOB support is included through `py-clob-client-v2`. Real live trading also
requires wallet/API credentials in `.env`.

Run tests:

```bash
python -m pytest tests/
```

Run targeted tests while iterating:

```bash
python -m pytest tests/test_execution_policy.py
python -m pytest tests/test_strategy_allocator.py
python -m pytest tests/test_strategy_execution.py
python -m pytest tests/test_mapping.py tests/test_sync_markets.py
python -m pytest tests/test_paper_trader.py tests/test_live_executor_policy.py
```

Useful diagnostics:

```bash
python check_config.py
python ops_readiness.py
python risk_config_audit.py
python analyze_logs.py
python outcome_attribution.py --help
```

There is no central formatter config in this repo. Keep style close to the file
you are editing: typed dataclasses, explicit guards, plain functions, and focused
tests.

## Testing Expectations

Run narrow tests for the code you changed. Run the full test suite when a change
touches any of these areas:

- `runtime/bot_runtime.py`
- `config.py` or `runtime_config.py`
- `execution_policy.py`
- `live_executor.py`
- `paper_trader.py`
- `live_position_store.py`
- `live_exit_engine.py` or `exit_policy.py`
- mapping, market sync, quarantine, or binder code
- strategy collection/allocation/execution
- storage schemas, log writers, or state migration code

Test patterns already used in this repo:

- Use `tmp_path` for SQLite/CSV/YAML test outputs.
- Monkeypatch `storage_v2.DEFAULT_DB_PATH` or constructor paths for isolated
  state tests.
- Use fake book stores and fake executors rather than network calls.
- Use `pytest.mark.asyncio` or `pytest.mark.anyio` for async runtime/executor
  behavior.
- Assert rejection reasons, policy fields, strategy metadata, and persisted
  state transitions, not just success/failure.

## Coding Conventions

- Prefer existing module boundaries and helper APIs over new abstractions.
- Keep runtime side effects visible and logged.
- Make risk-impacting changes explicit and covered by tests.
- Preserve deterministic strategy IDs and log join keys.
- Keep timestamps in nanoseconds where runtime code expects `*_ns`.
- Treat token IDs, market IDs, match IDs, lobby IDs, and league IDs as strings.
  Many are too large or too inconsistent for integer assumptions.
- Avoid global weakening of validation thresholds to fix one bad market. Add a
  targeted alias, exception, or test-backed rule.
- Use structured parsers for YAML/JSON/SQLite/Parquet instead of string hacks.
- Keep comments useful. Historical comments in this repo often explain why a
  safety rail exists; do not delete them unless replacing them with better
  evidence.

## Common Change Recipes

Add a strategy:

1. Add or update a strategy contract in `strategies/*.yaml` and registry usage.
2. Implement signal/reject objects and engine evaluation.
3. Add collection in `strategy_collection.py`.
4. Add allocator priority if needed in `strategy_allocator.py`.
5. Add execution in `strategy_execution.py`.
6. Add/extend policy gates in `execution_policy.py`.
7. Add logging fields to relevant loggers if new metadata matters.
8. Add tests for engine rejects/signals, allocation, execution, and policy.

Change a risk gate:

1. Find whether the gate belongs in engine-specific code, shared
   `execution_policy.py`, or `live_executor.py`.
2. Add tests for paper research, live parity, dry live, and real live behavior
   when applicable.
3. Check that rejection reasons are preserved into attempt logs.
4. Run config and policy tests.

Change mapping behavior:

1. Update `mapping_validator.py`, `mapping.py`, `sync_markets.py`, or binder code
   in the narrowest place possible.
2. Add examples to mapping tests for the exact ambiguous case.
3. Run mapping and sync tests.
4. Avoid changing runtime overlays or quarantine semantics unless required.

Change storage/logging:

1. Keep old rows readable.
2. Keep CSV headers stable unless every consumer is updated.
3. Add idempotent migration or compatibility handling.
4. Test with temp SQLite/CSV files.

## Files to Be Careful With

- `.env`: local secrets and live switches.
- `markets.yaml`: base market identity. A bad mapping can trade the wrong side.
- `logs/runtime_markets.yaml`: mutable runtime overlay.
- `logs/state_v2.sqlite`, `logs/state.sqlite`: live/paper state.
- `logs/live_positions*`, old JSON/SQLite state files: may exist from migration.
- `data_v2/operational.db`: operational DB for unified storage.
- `ops/*.service`: supervised production behavior.

If you need to inspect these files, do so read-only unless the user explicitly
asks for an operational change.

## Quick Orientation Checklist

For most code tasks:

1. Read the local module and its nearest tests.
2. Check whether the change affects paper, dry-live, or real-live paths.
3. Check whether strategy metadata or log schemas need updates.
4. Add or update targeted tests.
5. Run targeted tests; run full suite for shared runtime changes.
6. Report what changed and what was verified.

