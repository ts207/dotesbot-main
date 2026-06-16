# Repository Map

## Operational Runtime

- `supervisor.py`: production watchdog and process owner.
- `main.py`: core async bot loop.
- `config.py`: environment-backed runtime configuration.
- `steam_client.py`, `poly_ws.py`, `book_refresh.py`: Steam and Polymarket feed
  ingestion.
- `mapping.py`, `mapping_validator.py`, `sync_markets.py`,
  `auto_series_binder.py`: market discovery and Steam/Polymarket binding.
- `live_executor.py`, `live_exit_engine.py`, `live_position_store.py`,
  `live_state.py`, `live_reconciliation.py`: order, exit, position, and budget
  state handling.

## Strategies

- `value_engine.py`: primary value strategy.
- `decisive_swing_engine.py`: disabled-by-default BO3 convergence strategy.
- `continuous_engine.py`, `continuous_scorer.py`: snapshot momentum strategy.
- `arb_engine.py`, `arb_scanner.py`: YES/NO settlement arbitrage.
- `event_detector.py`, `signal_engine.py`, `event_taxonomy.py`: tactical event
  stack and live event allowlists.
- `scalp_executor.py`, `favorite_engine.py`, `early_favorite_shadow.py`,
  `ml_only_strategy.py`, `nomodel_event_strategy.py`: secondary or research
  strategy surfaces.

## State, Data, And Artifacts

- `markets.yaml`: mutable runtime market mapping file. Preserve.
- `.env`: local secrets and deployment config. Preserve and never print.
- `logs/`: runtime logs, positions, attempts, heartbeats, and monitor state.
  Preserve live/paper state when present.
- `data/`, `models/`, `dota_fair_model/models/`, `reports/`, `validations/`:
  research, model, and audit artifacts. Preserve unless a future pass classifies
  a specific file as generated junk.
- `unified_storage/`: newer SQLite/Parquet storage helpers. Currently supports
  research/backfill readers more than the live runtime path.

## Tests And Research

- `tests/`: unit and integration-style safety coverage.
- `scripts/`: analysis, preflight, migration, replay, and research utilities.
- `scratch/` and `scratch_*.py`: exploratory analysis. Do not prune beyond
  obvious generated junk without a separate classification pass.

## Cleanup Policy

Safe-to-delete generated junk:

- `*:Zone.Identifier`
- `__pycache__/`, `.pytest_cache/`, and `*.pyc`
- accidental empty files and known zero-byte scratch outputs after reference
  checks

Protected files:

- `.env`
- `markets.yaml`, `lol_markets.yaml`
- `logs/live_*`, `logs/paper_*`, position/state JSON, and heartbeats if present
- model/data/report artifacts not explicitly classified as junk
