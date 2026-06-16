# B4 Backtest Summary — 2026-05-25

Ran `python3 backtest_live_data.py` with live-bot-matching config:
- `--min-lag 0.05 --min-edge 0.05 --max-spread 0.15 --size 5 --exit 30 --max-book-age 90000`

## Aggregate

- **Events detected:** 2,737 (across full DreamLeague capture in `liveleague_raw.jsonl`)
- **Trades accepted:** 58 (2% acceptance rate)
- Trade-by-trade CSV: `validations/backtest_2026_05_25.csv`

## Top reject reasons (the funnel)

| Reason | Count | % of rejects |
|---|---:|---:|
| fill_price_too_high | 1285 | 47% |
| not_in_trade_events | 545 | 20% |
| book_stale | 160 | 6% |
| fight_swing_too_late | 124 | 5% |
| cooldown | 92 | 3% |
| volatility_spread_too_wide | 90 | 3% |
| missing_best_ask | 85 | 3% |
| fight_swing_too_early | 69 | 3% |
| insufficient_ask_size | 67 | 2% |
| lag_too_small | 67 | 2% |
| inactive_event | 49 | 2% |

## Per-event PnL (accepted trades only)

| event_type | n | mean@30s | win@30s | **mean@settle** | **win@settle** |
|---|---:|---:|---:|---:|---:|
| POLL_FIGHT_SWING | 33 | +0.121 | 52% | **+1.311** | **92%** |
| POLL_VALUE_DISAGREEMENT | 19 | −0.257 | 16% | **+1.782** | **75%** |
| POLL_TEAM_WIPE | 3 | −0.033 | 33% | −1.048 | 50% |
| POLL_ULTRA_LATE_FIGHT_FLIP | 2 | +0.600 | 50% | +2.600 | 100% |
| POLL_LATE_FIGHT_FLIP | 1 | −0.400 | 0% | +0.000 | 0% |

## Key findings (actionable)

1. **Hold-to-settle is dramatically better than the 30s horizon for the two high-n events.**
   - POLL_VALUE_DISAGREEMENT goes from −25¢ at 30s to **+1.78 at settlement (75% win)**.
   - POLL_FIGHT_SWING goes from +12¢ at 30s to **+1.31 at settlement (92% win)**.
   - Consider raising `EXIT_HORIZON_BY_EVENT` for these (currently 30s in `config.py:154`).

2. **POLL_VALUE_DISAGREEMENT is a breakout finding** — currently NOT in TRADE_EVENTS, but 19 trades show massive settlement edge. Worth promoting to TIER_B with `EXIT_HORIZON_BY_EVENT[POLL_VALUE_DISAGREEMENT] = 0` (hold to settlement). The price floor / threshold logic in `signal_engine.py:565-566` already gates it sensibly.

3. **Fill-price caps may be too tight.** 1285 of 2737 events (47%) rejected for `fill_price_too_high`. Combined with B1's per-bucket data, this is likely cutting good trades. Re-examine `_EVENT_MAX_FILL` for VALUE_DISAGREEMENT and FIGHT_SWING using a higher-ask sub-sample.

4. **research-tier events stay losers.** POLL_RAPID_STOMP (n=205 seen, 0 accepted), POLL_DECISIVE_STOMP (n=212 seen, 0 accepted) — backtest auto-rejects them. Validates the 2026-05-24 demotion.

5. **POLL_TEAM_WIPE and POLL_LATE_FIGHT_FLIP** have too few trades to draw conclusions (n=3 and n=1).

## Relaxed-cap re-run (2026-05-25)

Re-ran the backtest with `POLL_FIGHT_SWING` cap 0.82→0.94 and `POLL_VALUE_DISAGREEMENT` cap 0.45→0.85 (`scripts/backtest_relaxed_caps.py`, output `validations/backtest_2026_05_25_relaxed.csv`). Bucketed settlement PnL by ask:

| event_type | ask bucket | n | mean@S | win@S |
|---|---|---:|---:|---:|
| POLL_FIGHT_SWING | 0.30-0.45 | 5 | +1.91 | 80% |
| POLL_FIGHT_SWING | 0.45-0.60 | 5 | +1.68 | 80% |
| POLL_FIGHT_SWING | **0.60-0.75** | 11 | **+1.32** | **100%** |
| POLL_FIGHT_SWING | 0.75-0.85 | 5 | +0.02 | 80% |
| POLL_VALUE_DISAGREEMENT | <0.30 | 4 | +3.44 | 100% |
| POLL_VALUE_DISAGREEMENT | 0.30-0.45 | 9 | +1.43 | 67% |
| POLL_VALUE_DISAGREEMENT | **0.45-0.60** | 17 | **+1.70** | **88%** |
| POLL_VALUE_DISAGREEMENT | **0.60-0.75** | 36 | **+1.00** | **89%** |
| POLL_VALUE_DISAGREEMENT | 0.75-0.85 | 44 | +0.16 | 84% |

**Applied:**
- POLL_FIGHT_SWING dynamic favorite cap raised 0.60 → 0.75 (`signal_engine.py:566`). Captures the 0.60-0.75 bucket where the bulk of high-win trades sit.
- POLL_VALUE_DISAGREEMENT `_EVENT_MAX_FILL` raised 0.45 → 0.75 (`signal_engine.py:95`).

**Followup applied 2026-05-25 (user authorized "A — remove the gate"):** removed three "underdog" gates that were blocking POLL_VALUE_DISAGREEMENT from realizing the new cap: (a) `if primary_event_type == "POLL_VALUE_DISAGREEMENT" and not is_underdog_reversal_by_ask` at `signal_engine.py` ~line 576 (the ask-based gate); (b) the `not_an_underdog` check at ~line 646 (current-price-based gate); (c) the `price_not_deep_enough` check at ~line 648 (current_price >= 0.45 hard floor). Semantic: POLL_VALUE_DISAGREEMENT is no longer "underdog comeback only" — it's "any value disagreement up to ask=0.75". `is_underdog_reversal` is still computed for downstream signed-edge / lag tuning, but no longer gates entry. The 0.75 cap is now the binding entry constraint.

