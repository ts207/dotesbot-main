# Effective Runtime Config

`runtime_config.py` is the typed source of truth for high-risk runtime settings.
`config.py` still exports the legacy module constants used across the bot, but
those constants now come from `RUNTIME_CONFIG` for the settings below.

Run:

```bash
python check_config.py
```

The checker prints:

```text
setting
runtime value
source
safe_for_paper
safe_for_dry_live
safe_for_real_live
```

`source` is `env` when a value came from the process environment or `.env`, and
`default` when the repository default was used.

## Typed Sections

- `feed`: Steam polling, Steam snapshot age, source update freshness, TopLive
  requirement.
- `book`: CLOB book age, spread cap, ask-size floor.
- `signal`: executable-edge and lag gates.
- `paper`: paper mode, sizing, slippage, execution delay, per-match exposure.
- `live`: live mode and live risk caps.
- `strategy`: VALUE, DSWING, and EVENT_TRIGGERED_VALUE enable switches.

## Live Mode

`runtime_config.LiveConfig.live_mode` is derived from the existing switches:

- `off`: `LIVE_TRADING=false` and `ENABLE_REAL_LIVE_TRADING=false`
- `dry_run`: `LIVE_TRADING=true` and `ENABLE_REAL_LIVE_TRADING=false`
- `real`: `ENABLE_REAL_LIVE_TRADING=true`

Real live mode fails closed if required live settings are missing, using defaults,
or fail the real-live safety check. This prevents accidental real trading from
repository defaults.

## Paper Mode

`PAPER_MODE` controls how the simulator treats live-gate failures:

- `research`: enter counterfactual paper positions and label live rejection fields.
- `live_parity`: reject paper entries that would not pass live gates.
- `shadow_live`: only enter paper when the dry-live policy would submit.

Required explicit real-live settings:

```text
MAX_TOTAL_LIVE_USD
MAX_TRADE_USD
MAX_OPEN_POSITIONS
MAX_DAILY_DRAWDOWN_USD
MAX_STEAM_AGE_MS
MAX_SOURCE_UPDATE_AGE_SEC
MAX_BOOK_AGE_MS
MAX_SPREAD
MIN_ASK_SIZE_USD
MIN_LAG
MIN_EXECUTABLE_EDGE
```

## Defaults

Defaults in `runtime_config.py` intentionally match `.env.example` for the
centralized settings, so runtime behavior no longer depends on whether `.env`
exists.
