# Design: Exit Observation Ledger (Batch 11)

## Goal
Add an observation ledger that records exit decisions and outcomes to evaluate the effectiveness of various exit rules (catastrophe salvage, fair invalidation, etc.) without changing trading behavior.

## Architecture
- **Module:** `exit_observation.py`
- **Storage:** `logs/exit_policy_observations.csv` (CSV format)
- **Integration:** Hook into `PaperTrader` exit loop in `bot_runtime.py`.

## Components

### 1. `build_exit_observation_row`
A pure helper function that constructs a dictionary representing one observation row.

**Inputs:**
- `position`: dict (from `Position` or `ClosedPosition`)
- `book`: dict | None (current book snapshot)
- `game`: dict | None (current game snapshot)
- `game_over_match_ids`: set[str]
- `actual_exit_reason`: str | None
- `actual_exit_price`: float | None
- `settlement_price`: float | None (optional, usually unknown at exit)
- `now_ns`: int | None

**Logic:**
- Extract position metadata (ID, match, token, side, strategy).
- Calculate `age_sec`.
- Capture current book prices (`bid`, `ask`).
- Evaluate counterfactual triggers by checking conditions for:
    - `catastrophe_salvage`: bid < floor AND radiant_lead confirms loss.
    - `fair_invalidation`: current_fair drops below entry and bid by buffers.
    - `map_end_convergence`: match_id in `game_over_match_ids` (specifically for DSWING/convergence).
    - `game_over`: match_id in `game_over_match_ids`.
    - `max_hold`: age_sec >= MAX_HOLD_HOURS.
- Calculate PnL if `actual_exit_price` is provided.
- Initialize settlement columns to None/empty.

### 2. `write_exit_observation`
Appends a row to `logs/exit_policy_observations.csv`.
- Ensures headers are written if the file is new.
- Uses a deterministic column order.

## Integration Point
In `runtime/bot_runtime.py`, after `trader.check_exits(...)` and within the `for cp in closed:` loop:
```python
for cp in closed:
    # ... existing logging ...
    row = build_exit_observation_row(
        position=cp.to_dict(),
        book=book_store.get(cp.token_id),
        game=last_steam_games.get(cp.match_id) if last_steam_games else None,
        game_over_match_ids=game_over_match_ids,
        actual_exit_reason=cp.exit_reason,
        actual_exit_price=cp.exit_price,
        now_ns=cp.exit_time_ns,
    )
    write_exit_observation(row)
```

## Testing Plan
- `test_observation_row_no_exit`: verify metadata and age calculation.
- `test_observation_row_catastrophe_triggered`: verify floor and NW confirm logic.
- `test_observation_row_fair_invalidation_triggered`: verify fair buffer logic.
- `test_observation_row_game_over_triggered`: verify match_id in set.
- `test_observation_row_settlement_counterfactual`: verify PnL calculation when settlement is provided.
- `test_write_exit_observation_stable_header`: verify CSV writing and header persistence.

## Dependencies
- `exit_policy.py`: for rule definitions and helper functions.
- `config.py`: for thresholds.
- `csv`, `time`, `os`, `datetime`.
