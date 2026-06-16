# Backtest Vs Live Current Snapshot

Generated: 2026-05-25

Command:

```bash
python3 backtest_live_data.py --diagnostics --csv-out validations/backtest_live_data_current.csv
```

## Backtest Result

- Total trades: 34
- 30s horizon: avg +0.144, total +4.90, wins 17/34, profit factor 2.24
- 60s horizon: avg +0.144, total +4.90, wins 18/34, profit factor 1.95
- Settlement subset: n=25, total +32.07, wins 23/25

Accepted event mix:

- `POLL_FIGHT_SWING`: 31 trades, avg30 +0.132, W30 52%
- `POLL_ULTRA_LATE_FIGHT_FLIP`: 2 trades, avg30 +0.600, W30 50%
- `POLL_LATE_FIGHT_FLIP`: 1 trade, avg30 -0.400

Diagnostics:

- Events detected: 1190
- Trades accepted: 34
- Largest rejects: `not_in_trade_events` 591, `book_stale` 134, `fight_swing_too_late` 124, `fill_price_too_high` 97
- `POLL_DECISIVE_STOMP` and `POLL_RAPID_STOMP` were seen but accepted 0 times in current backtest rules.

## Live Comparison

From `validations/current_event_markouts.md` / `logs/live_attempts.csv` post-2026-05-19:

- `POLL_RAPID_STOMP`: 17 submit-phase attempts, $70 submitted, $0 confirmed filled, statuses mostly `delayed`
- `POLL_DECISIVE_STOMP`: 6 submit-phase attempts, $10 submitted, $0 confirmed filled
- `POLL_LEAD_FLIP_WITH_KILLS`: 1 rejected precheck

Interpretation:

- Backtest is now trading the current allowed mix (`POLL_FIGHT_SWING`, `POLL_ULTRA_LATE_FIGHT_FLIP`, `POLL_LATE_FIGHT_FLIP`), not the pre-demotion RAPID/DECISIVE live mix.
- The live-vs-backtest divergence is dominated by stale live data and the fixed delayed-fill accounting bug: historical delayed rows were treated as accepted/submitted but never resolved into terminal fill/cancel rows.
- After A1, new delayed orders should append `phase=resolution` rows and update filled accounting, making future live/backtest comparison valid.
