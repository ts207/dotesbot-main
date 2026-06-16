# Runbook

This repo is scoped to two paper strategies:

- `VALUE`: map-winner value entries from `value_engine.py`.
- `DSWING`: BO3 match-winner decisive-swing entries from `decisive_swing_engine.py`.

Real CLOB submission stays disabled unless `ENABLE_REAL_LIVE_TRADING=true`.
Current operating mode should keep:

```bash
LIVE_TRADING=true
ENABLE_REAL_LIVE_TRADING=false
VALUE_ENGINE_ENABLED=true
ENABLE_VALUE_TRADING=true
DSWING_ENABLED=true
DSWING_SHADOW_ENABLED=false
UNIFIED_STORAGE_DUAL_WRITE=false
```

## Preflight

```bash
.venv/bin/python scripts/preflight.py
```

Preflight should report value and DSWING module health, paper guard status, writable logs, mappings, and optional historical data availability.

## Start Or Restart

The supervisor owns the runtime:

```bash
.venv/bin/python supervisor.py
```

To reload code/config while supervisor is running, terminate only the `main.py`
child and let the supervisor restart it.

## Runtime Files

- `logs/value_attempts.csv`: value signals and rejects.
- `logs/dswing_attempts.csv`: DSWING signals and rejects.
- `logs/paper_attempts.csv`: paper entry attempts from value and DSWING.
- `logs/paper_positions.json`: open paper positions.
- `logs/paper_exits.csv`: paper exits.
- `logs/heartbeat`: process heartbeat for runtime verification.

## Verify Runtime

```bash
.venv/bin/python scripts/verify_paper_runtime.py --json
```

Required checks:

- `ok: true`
- `enable_real_live_trading: false`
- exactly one supervisor, main, binder, shadow, and monitor process
- fresh bot heartbeat

## Promotion Rule

Do not set `ENABLE_REAL_LIVE_TRADING=true` from this runbook. Real trading needs a separate capital, fills, settlement, and risk review.
