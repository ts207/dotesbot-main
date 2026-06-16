# Repository Map

## Runtime

- `supervisor.py`: process watchdog.
- `main.py`: async bot loop; wires value and DSWING paper entries.
- `config.py`: environment-backed runtime configuration.
- `steam_client.py`, `poly_ws.py`, `book_refresh.py`: Steam and Polymarket feeds.
- `mapping.py`, `mapping_validator.py`, `sync_markets.py`, `auto_series_binder.py`: market discovery and binding.
- `live_executor.py`, `live_exit_engine.py`, `live_position_store.py`, `live_state.py`, `live_reconciliation.py`: guarded paper/live execution state.

## Strategies

- `value_engine.py`: map-winner value strategy.
- `decisive_swing_engine.py`: BO3 match-winner decisive-swing strategy.
- `event_detector.py`, `signal_engine.py`, `event_taxonomy.py`: event diagnostics and signal logging only; event entries are disabled in `main.py`.

## Storage And Logs

- `storage.py`: CSV loggers. Runtime canonical logs are under `logs/*.csv`.
- `unified_storage/`: optional historical/backfill storage helpers. Live dual-write is disabled unless `UNIFIED_STORAGE_DUAL_WRITE=true`.
- `logs/value_attempts.csv`: value decisions.
- `logs/dswing_attempts.csv`: DSWING decisions.
- `logs/paper_attempts.csv`, `logs/paper_positions.json`, `logs/paper_exits.csv`: paper execution ledger.

## Data And Artifacts

- `markets.yaml`: active market mapping file.
- `.env`: local runtime config and secrets. Do not print secrets.
- `data/`, `data_v2/`, `models/`, `reports/`, `validations/`: data, model, and audit artifacts.
- `scripts/`: retained operational, value, DSWING, mapping, and audit utilities.
- `scratch/`: exploratory analysis; not imported by runtime.

## Cleanup Policy

Safe generated junk:

- `__pycache__/`, `.pytest_cache/`, `*.pyc`
- `*:Zone.Identifier`
- local UI backup folders

Protected runtime state:

- `.env`
- `markets.yaml`
- `logs/live_*`, `logs/paper_*`, positions, heartbeats
- model and data artifacts unless explicitly classified as disposable
