# Paper Run Status

Generated: 2026-06-15 15:48 UTC

## Configuration

- `MODE=paper`
- `LIVE_TRADING=true`
- `ENABLE_REAL_LIVE_TRADING=false`
- `EVENT_DETECTORS_ENABLED=true`
- `CONTINUOUS_ENGINE_ENABLED=false`
- `ENABLE_CONTINUOUS_TRADING=false`
- `ARB_ENGINE_ENABLED=false`
- `ENABLE_ARB_TRADING=false`

Real CLOB order submission is disabled. The guarded live executor is wired only
to the paper attempt path.

## Readiness Checks

- `.venv/bin/python scripts/preflight.py`: passed for current paper mode.
- `.venv/bin/python -m py_compile scripts/verify_paper_runtime.py`: passed.
- `.venv/bin/python scripts/verify_paper_runtime.py`: passed.
- Focused tests after runtime patches:
  - `tests/test_match_winner_integration.py`
  - `tests/test_live_trading_safety.py`
  - `tests/test_signal_engine.py`
  - `tests/test_mapping.py`
  - `tests/test_match_winner_mapping.py`
  - `tests/test_main_signal_selection.py`
- Latest focused regression set: 35 passed, 1 skipped.

## Runtime

- Supervisor is running detached.
- Managed children are running:
  - `main.py`
  - `auto_series_binder.py --loop`
  - `settlement_shadow.py --loop`
  - `monitor.py --loop`
- Heartbeats are fresh for bot, binder, shadow, and monitor.
- Startup mapping scope was fixed so match-winner trading is included at startup,
  not only after the periodic refresh.
- Startup market discovery was delayed so the first Steam poll is not blocked by
  a full Polymarket scrape.
- Startup invalid-mapping logging is capped to avoid slow/noisy startup.

## Mapping State

- Valid mappings: 188
- Active paper-trading mappings: 188
- Active mapping types:
  - `MATCH_WINNER`: 61
  - `MAP_WINNER`: 127
- Invalid/provisional mappings remain excluded until they have real Steam match
  IDs and `confidence: 1.0`.

## Live Overlap

At the verification point:

- Live Steam games fetched: 53
- Active mapped Steam match ID overlap: 1
- Overlap match ID: `8853138000`
- Market: `Dota 2: Power Rangers vs Spirit Academy - Game 1 Winner`
- Recent overlap signals: 10
- Recent clean overlap signals: 6

The active overlap is now mapped cleanly:

- `mapping_confidence=1.0`
- `mapping_errors` empty
- Team alias fix maps Steam `_PowerRangers` to Polymarket `Power Rangers`

The running bot is evaluating the mapped live game and correctly refusing entry
because the market is effectively terminal/high price:

- Latest signal decision: `skip`
- Latest signal skip reason: `event_type_inactive`
- Latest value reject reason: `price_too_high`
- Latest value ask: `0.99`

No runtime paper attempts or paper trades have been written yet because the only
current mapped opportunity is not executable under the configured strategy
guards.

## Trade Path Smoke

`scripts/verify_paper_runtime.py` now exercises both paper trade paths without
mutating runtime logs or submitting orders:

- `PaperTrader` synthetic fill: filled at `0.50`, cost `$5.00`, shares `10.0`.
- `LiveExecutor.try_buy` with `ENABLE_REAL_LIVE_TRADING=false`: filled
  `paper_simulated`, submitted `$1.00`, filled `$1.00`, average fill `0.50`,
  with state save patched in-memory.

## Evidence Files

- `logs/supervisor.log`
- `logs/heartbeat`
- `logs/binder_heartbeat`
- `logs/shadow_heartbeat`
- `logs/monitor_heartbeat`
- `logs/book_events.csv`
- `logs/paper_attempts.csv`
- `logs/paper_trades.csv`
- `scripts/verify_paper_runtime.py`
