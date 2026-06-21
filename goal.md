You are investigating whether the MODEL_VALUE_EDGE paper/backtest results are actually true, reproducible, and not an artifact of code bugs, leakage, stale books, unresolved labels, or cherry-picked reporting.

Work in the repo directly. Do not assume any previous claim is correct. Verify every claim from source files, scripts, logs, and regenerated outputs.

Primary claims to verify:

1. The residual-mode default threshold is really 0.02 unless overridden.
2. The current paper run is using MODEL_VALUE_MIN_EDGE=0.01 and MODEL_VALUE_CONFIRM_MIN_EDGE=0.01 as runtime overrides.
3. The engine is constrained to 7m–40m game time.
4. The predictor correctly handles missing TopLive net worth / score fields as NaN.
5. The Python tree evaluator handles NaN the same way LightGBM does, using the model’s default_left behavior.
6. The replay-safe clock is actually used for book_age_ms and policy now_ns.
7. strategy_family="MODEL_VALUE" is passed into PolicyInput so model-value risk blocks apply.
8. confirmation_reason and confirmed are logged correctly.
9. The 0.01 vs 0.02 paired comparison is reproducible.
10. The reported 65 trades, 75.38% win rate, +8.65% ROI, and +$28.12 PnL are reproducible.
11. The result is not dominated by one or a few trades.
12. The 0.01 threshold is genuinely better because it enters earlier / better, not because of stale-book or replay artifacts.
13. The 40m+ bucket is actually negative and is excluded from paper execution.
14. The live paper process has made zero real-live attempts.

Required procedure:

A. Inspect current code

Read and summarize the relevant sections of:

* config.py
* model_value_predictor.py
* model_value_engine.py
* strategy_collection.py
* strategy_execution.py
* execution_policy.py
* strategies/model_value_edge.yaml
* models/dota_lgbm_win/metadata.json
* models/dota_lgbm_win/features.json

Specifically verify:

* MODEL_VALUE_EDGE_MODE
* DEFAULT_MODEL_VALUE_MIN_EDGE
* MODEL_VALUE_MIN_EDGE
* MODEL_VALUE_CONFIRM_MIN_EDGE
* MODEL_VALUE_MIN_GAME_TIME_SEC
* MODEL_VALUE_MAX_GAME_TIME_SEC
* MODEL_VALUE_MAX_SPREAD
* MODEL_VALUE_MAX_BOOK_AGE_MS
* model residual_mode
* model feature list
* NaN/default_left behavior
* book_age_ms calculation
* PolicyInput.signal fields
* confirmation logging
* real-live disabled state

B. Re-run tests

Run the relevant unit/integration tests for:

* residual threshold defaults
* NaN/default_left tree evaluation
* missing net worth feature path
* strategy_family plumbing
* replay-safe clock/book_age_ms
* confirmation_reason logging
* paper-only / real-live disabled behavior

If tests are missing, write minimal tests before making claims.

C. Reproduce the replay backtest

Find the exact backtest scripts and data inputs used for:

* baseline 0.02 run
* 0.01 threshold run
* 0.01 vs 0.02 paired comparison
* game-time segmentation
* spread sensitivity
* confirmation sensitivity
* book-age sensitivity
* ask-band sensitivity
* profit concentration
* CLV split

Re-run from scratch. Save fresh outputs under a new timestamped report directory, for example:

reports/model_value_audit_YYYYMMDD_HHMMSS/

Do not overwrite prior reports.

D. Validate data integrity

Check:

* number of source snapshot rows
* number of valid replay rows
* number of unique matches
* number of unique resolved matches
* duplicate snapshot rate
* duplicate trade rate
* whether train/validation/backtest data overlap
* whether settlement outcomes are joined using future information only after trade simulation
* whether all 65 trades truly have known terminal outcomes
* whether any unresolved trades are hidden or dropped
* whether market_id/token_id mapping is consistent
* whether side mapping normal/reversed is correct
* whether the same match can produce more than one trade
* whether opposing-token trades are blocked

E. Validate execution realism

For each generated trade, compute and report:

* entry timestamp
* match_id
* token_id
* side
* game_time_sec
* bid
* ask
* spread
* book_age_ms
* ask_size if available
* market_mid
* model_probability
* predicted_residual
* edge
* confirmation_reason
* fill price assumption
* settlement outcome
* pnl
* roi
* CLV_30s
* CLV_120s
* CLV_300s
* CLV_900s
* CLV_1200s

Flag trades where:

* book_age_ms exceeds config
* spread exceeds config
* game_time_sec outside 420–2400
* model_version missing
* token_net_worth_lead is NaN
* score_margin is NaN
* edge is barely above threshold
* fill would require unavailable liquidity
* market appears stale/dead
* settlement outcome is missing
* same match had conflicting earlier/later signals

F. Verify the 0.01 vs 0.02 paired comparison

Produce three tables:

1. Common trades

   * match_id
   * token_id
   * side
   * entry_ts_0.01
   * entry_ts_0.02
   * seconds_earlier
   * ask_0.01
   * ask_0.02
   * ask_improvement
   * edge_0.01
   * edge_0.02
   * CLV delta
   * PnL delta

2. 0.01-only trades

   * same fields
   * settlement outcome
   * CLV
   * whether 0.02 later entered same match on opposite token

3. 0.02-only trades

   * same fields
   * settlement outcome
   * CLV
   * whether 0.01 earlier occupied the match slot

Confirm or refute the claim:

“0.01 is better because it enters the same real positives earlier and prevents later wrong-side entries.”

G. Stress-test the result

Run sensitivity grids:

* edge threshold: 0.005, 0.01, 0.015, 0.02, 0.03, 0.04, 0.05, 0.075, 0.10
* max game time: 1200, 1800, 2400, 3000, unlimited
* min game time: 0, 300, 420, 600, 900
* max spread: 0.02, 0.03, 0.04, 0.05, 0.06, 0.08
* max book age: 1000, 2500, 5000, 7500, 10000
* confirmation max age: 15, 30, 60, 90, 120
* ask worsen: 0.00, 0.01, 0.02, 0.03

For each grid row, report:

* trades
* resolved trades
* win rate
* ROI
* PnL
* avg ask
* avg spread
* avg book_age_ms
* avg edge
* CLV_1200s
* top 1 contribution
* top 3 contribution
* ROI excluding best 1
* ROI excluding best 3

H. Check live paper logs

Inspect current logs:

* logs/strategy_signals.csv
* logs/strategy_allocator.csv
* logs/paper_attempts.csv
* logs/paper_exits.csv
* logs/paper_positions_v2.json
* logs/live_attempts.csv if present

Verify:

* paper process is producing signals
* no real-live orders were submitted
* no entries outside 420–2400 seconds
* no entries above spread/book-age limits
* all model entries have model_version
* confirmation_reason exists
* paper fills are at realistic ask prices
* current open positions are sensible
* CLV is being tracked or can be reconstructed

I. Final report format

Write a final Markdown report with:

1. Executive verdict:

   * TRUE / PARTIALLY TRUE / FALSE for each major claim.
2. Reproducibility:

   * exact command lines used
   * git commit hash
   * data file paths
   * generated report paths
3. Code audit findings:

   * confirmed fixes
   * remaining bugs
   * risky assumptions
4. Backtest reproduction:

   * tables and summary stats
5. Paired threshold comparison:

   * common / unique trade analysis
6. Live paper audit:

   * current runtime config
   * current log evidence
   * real-live safety verification
7. Recommendation:

   * keep 0.01 or revert to 0.02
   * keep 7m–40m gate or change it
   * continue paper / stop paper / dry-live later
8. Blockers before real live.

Be skeptical. Do not use phrases like “fantastic,” “smoking gun,” or “ready for live” unless the data supports it. Prefer exact numbers and reproducible evidence. If any claim cannot be reproduced, say so explicitly.
