# Bot Logic and Strategy Audit

Generated as a repository cleanup baseline. This document describes the current
runtime shape and strategy status without changing live behavior.

## Runtime Flow

Production should start with `python3 supervisor.py`. The supervisor owns four
child processes: `main.py`, `auto_series_binder.py --loop`,
`settlement_shadow.py --loop`, and `monitor.py --loop`. Each child writes a
heartbeat file; the supervisor restarts dead or stale processes.

`main.py` is the core async loop. It loads `markets.yaml`, syncs live Steam games
to Polymarket markets, subscribes to books, polls Steam, logs snapshots/events,
runs enabled strategy engines, and routes fills/exits through the live or paper
executor path. Real order submission remains gated by
`ENABLE_REAL_LIVE_TRADING=true`.

The runtime still uses file state as the source of truth: `markets.yaml` for
market mappings and `logs/*.csv` / `logs/*.json` for attempts, positions, budget
state, heartbeats, and monitoring. `unified_storage/` defines a newer SQLite and
Parquet direction, but it is not the primary runtime store.

## Strategy Inventory

- Value engine: primary hold-to-settlement strategy. It backs the net-worth
  leader when top-live state, market scope, book freshness, price, edge, and
  orientation guards pass.
- Decisive swing: BO3 moneyline convergence strategy. It is wired but defaults
  off with `DSWING_ENABLED=false`.
- Continuous engine: snapshot-to-snapshot momentum scorer. It defaults off and
  is intended for shadow or explicitly enabled paper/live attempts.
- Arb engine: YES+NO settlement arb scanner. It defaults off and only trades when
  `ENABLE_ARB_TRADING=true`.
- Event detector / signal engine: legacy tactical event stack. It is still wired
  but heavily gated by event taxonomy, cadence quality, freshness, and live
  allowlists.
- Scalp, favorite, early-favorite, and model-B style paths are research,
  shadow, or disabled surfaces unless explicitly enabled.

## Safety Gates

Important guard layers are duplicated intentionally across strategy and executor
boundaries:

- Live order submission requires both live infrastructure and
  `ENABLE_REAL_LIVE_TRADING=true`.
- Mapping validation rejects placeholders, duplicate active match IDs, invalid
  market types, low confidence, team/name/league/series mismatches, and token
  identity issues.
- Value entries require top-live snapshots, supported market scope, fresh books,
  sane price bands, edge floors/caps, and orientation-flip protection.
- Executor gates enforce order type, event tier, event schema, cadence quality,
  event quality, spread, ask size, book age, Steam age, disk space, balance, open
  position count, total submitted budget, daily drawdown, and per-match exposure.
- Exit logic keeps value and arb positions hold-to-settlement except for game
  over, max-hold timeout, and narrow catastrophe salvage rules.

## Current Blockers

- The checkout has no `.git` metadata, so cleanup must keep its own manifests.
- `README.md` referenced `.env.example`, but a real example file was missing.
- Dependency metadata was too narrow for tests and research readers; several
  imported packages were absent from `requirements.txt`.
- Startup documentation drifted between `main.py` and `supervisor.py`.
- The repo contained many Windows `:Zone.Identifier` sidecar files and Python
  cache artifacts.
- Full test execution required creating a fresh environment because the system
  Python had no `pytest` or core runtime dependencies installed.
- Current verification is not green: see `reports/verification_summary.md` for
  the exact preflight and pytest failure groups. The failures are left for a
  separate behavior/test-alignment pass because this cleanup pass does not change
  live or strategy behavior.

## Non-Goals For This Pass

- No strategy threshold changes.
- No live-trading behavior changes.
- No credentialed exchange calls.
- No deletion of `.env`, `markets.yaml`, runtime state, model artifacts, data, or
  reports needed for audit.
