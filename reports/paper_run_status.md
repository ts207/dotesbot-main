# Paper Run Status

Generated: 2026-06-16 01:24 UTC / 2026-06-16 09:24 Asia/Ulaanbaatar

## Configuration

- `MODE=paper`
- `LIVE_TRADING=true`
- `ENABLE_REAL_LIVE_TRADING=false`
- `VALUE_ENGINE_ENABLED=true`
- `ENABLE_VALUE_TRADING=true`
- `EVENT_DETECTORS_ENABLED=true`
- `DSWING_SHADOW_ENABLED=false`
- `DSWING_ENABLED=true`
- `UNIFIED_STORAGE_DUAL_WRITE=false`

Real CLOB order submission remains disabled. Value entries are enabled only for
the guarded paper path. DSWING now uses the same guarded paper path:
`DSWING_ENABLED=true` can create paper attempts/positions while
`ENABLE_REAL_LIVE_TRADING=false` prevents real CLOB submission. Runtime data now
has one canonical live location: `logs/*.csv`. `data_v2/` remains available for
historical backfill/replay or an explicit opt-in dual-write run.

## Verification

- `.venv/bin/python scripts/preflight.py`: passed.
- `.venv/bin/python scripts/verify_paper_runtime.py --json`: passed.
- Full remaining test suite after repo cleanup:
  - `.venv/bin/python -m pytest -q`
  - Result: 243 passed, 2 skipped.
- Focused regression after DSWING decision logging:
  - `tests/test_dswing_shadow.py`
  - `tests/test_gettoplive_state.py`
  - `tests/test_logger_flush.py`
  - Result: 15 passed.
- Earlier focused regression after logging/value instrumentation:
  - `tests/test_logger_flush.py`
  - `tests/test_steam_client.py`
  - `tests/test_unified_storage_value_attempts.py`
  - `tests/test_gettoplive_state.py`
  - Result: 18 passed.

## Runtime

- Supervisor PID: `35162`.
- Current `main.py` PID: `155731`.
- Current `auto_series_binder.py` PID: `128921`.
- Managed children are running:
  - `main.py`
  - `auto_series_binder.py --loop`
  - `settlement_shadow.py --loop`
  - `monitor.py --loop`
- Bot heartbeat age at verification: under 1 second.
- `main.py` was intentionally restarted at 2026-06-16 01:46 UTC to load the
  value+DSWING-only paper runtime.

## Data And Log Fixes

- Stopped default dual-writing from live CSV logs into `data_v2/`.
- Confirmed no new `data_v2` files were created after the 2026-06-16 07:43:30
  Asia/Ulaanbaatar restart.
- Sanitized existing Steam error URLs in runtime logs so `key=` values are
  redacted.
- Added Steam error redaction before logging new fetch failures.
- Added a `value_attempts` unified-storage schema for explicit opt-in/backfill,
  but live `value_attempts` remains CSV-only under current config.
- Aligned `scripts/verify_paper_runtime.py` heartbeat thresholds with
  `supervisor.py`, fixing the false stale-shadow failure.
- Added DSWING decision logging to `logs/dswing_attempts.csv` and enabled
  DSWING paper entries while real trading remains disabled.
- Removed dormant continuous, arb, scalp, favorite, early-favorite, and nomodel
  strategy branches from runtime wiring, tests, examples, and stale research
  scripts. Event detection remains diagnostics-only.

## Mapping State

- Valid mappings: 194.
- Active paper-trading mappings: 194.
- Active mapping types:
  - `MAP_WINNER`: 133
  - `MATCH_WINNER`: 61
- Invalid/provisional mappings remain excluded until they have real Steam match
  IDs and `confidence: 1.0`.

## Live Overlap

At the verification point:

- Live Steam games fetched: 41.
- Active mapped Steam match ID overlap: 0.
- No current overlap signal or value attempt was present at verification time.

## Trade Path

No paper attempts or paper trades have been written yet. The bot is armed for
paper value entries and will write to `logs/paper_attempts.csv` once a value
candidate has a valid executable ask. DSWING will only write research rows to
`logs/dswing_attempts.csv`; it is not part of the paper-entry path yet.

`scripts/verify_paper_runtime.py` still exercises both paper trade paths without
mutating runtime logs or submitting orders:

- `PaperTrader` synthetic fill: filled at `0.50`, cost `$5.00`, shares `10.0`.
- `LiveExecutor.try_buy` with `ENABLE_REAL_LIVE_TRADING=false`: filled
  `paper_simulated`, submitted `$1.00`, filled `$1.00`, average fill `0.50`.

## Evidence Files

- `logs/supervisor.log`
- `logs/heartbeat`
- `logs/value_attempts.csv`
- `logs/dswing_attempts.csv`
- `logs/book_events.csv`
- `logs/paper_attempts.csv`
- `scripts/verify_paper_runtime.py`
- `value_engine.py`
