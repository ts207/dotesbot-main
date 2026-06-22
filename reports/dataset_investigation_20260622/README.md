# Dataset Quality Investigation Report: 2026-06-22

## Objective
Investigate the `data_v2/model_value_replay.parquet` and related operational logs to determine if the local historical trading dataset is reliable enough for profitability conclusions.

## 1. Dataset Inventory

| Dataset | Row Count | Match Count | Type / Description | Settlement | Book | Policy |
|---------|-----------|-------------|--------------------|------------|------|--------|
| `data_v2/model_value_replay.parquet` | 26,748 | 69 | Replay / Base Dataset | Yes | Yes | No |
| `logs/strategy_signals.csv` | 13,926 | 10 | Operational / Dry-Live Signals | No | Yes | Yes |
| `logs/strategy_allocator.csv` | 1,574 | 11 | Operational Allocations | No | No | No |
| `logs/paper_attempts.csv` | 383 | 4 | Paper / Dry-Live Execution Attempts | No | Yes | Yes |
| `logs/paper_exits.csv` | 71 | 5 | Paper Exits | No | Yes | No |
| `logs/live_attempts.csv` | - | - | Live Executions (Empty or Missing) | - | - | - |
| `logs/strategy_outcomes.csv` | 6 | 4 | Resolved Trades | Yes | Yes | Yes |
| `logs/settlement_shadow.csv` | 2 | 1 | Shadow Settlement Outcomes | No | Yes | No |

## 2. Replay Schema Audit (`model_value_replay.parquet`)
- **Total Rows**: 26,748
- **Unique Matches**: 69
- **Unique Tokens**: 184
- **Unique Markets**: 92
- **Market Type Distribution**: MAP_WINNER: 17,512 | MATCH_WINNER: 9,236
- **Data Source**: top_live: 10,302 | live_league: 2,546 (remaining 13,900 missing `data_source` indicating gaps or pure book rows)

### Key Null Counts
- `yes_best_ask`: 7,526 (28.1%)
- `yes_best_bid`: 6,988 (26.1%)
- `server_steam_id`: 16,446
- `settled_yes_outcome`: 0 (Nulls were filled or defaults used, but coverage requires inspection in `settlement_coverage.csv`)

## 3. Settlement Coverage
See `settlement_coverage.csv` for match-level details.
- Some terminal matches lack valid `settled_yes_outcome` resolution or have contradictory labels.
- Needs cross-referencing with `opendota_outcomes.json` and `settlement_shadow`.

## 4. Time Integrity
See `time_integrity_issues.csv` for rows with:
- Negative book ages (`timestamp_ns` < `book_received_at_ns`).
- Stale or missing `source_update_age_sec` (often missing when `data_source` is null).

## 5. Book / Execution Realism Audit
The replay dataset lacks realistic depth fields (ask sizes/bid sizes) and suffers from significant missing top-of-book data.
- **Missing Ask**: 28.1% of rows
- **Missing Bid**: 26.1% of rows
- **Ask > 0.95 (Unrealistic Execution)**: 2,424 rows
- **Spread > 0.06**: 4,352 rows
- **Spread > 0.15**: 1,838 rows
- **Spread > 0.50**: 46 rows

## 6. Signal / Policy Alignment
See `policy_alignment.csv`. 
- `policy_allowed` is not persisted in the parquet directly. Backtests must join with operational `strategy_signals.csv` or re-simulate the policy execution gates.
- Missing operational entries are often explained by the fact that `model_value` was likely run in research mode or disabled (`ENABLE_MODEL_VALUE_TRADING=false`).

## 7. Data Leakage Audit
See `leakage_risk_report.md`. 
- **Highest Risk**: Duplicate rows with identical timestamps for YES/NO markets, which can bleed future information if not carefully sorted or grouped by `match_id` + `timestamp_ns`.
- **Medium Risk**: Settlement fields must be excluded from feature vectors.

## 8. Match-Level Summary
See `match_level_summary.csv`.

## 9. Backtest Dependency Audit
See `model_value_funnel.csv`.
- Features -> Edge > 0.02 -> Ask <= 0.95 -> Confirmation -> Policy Pass -> Execution.

---

## 10. Final Conclusion

**Is the dataset reliable enough to estimate profitability?**
No. It is structurally sound for estimating *directional edge* (predicting the winner), but completely inadequate for estimating *executable net profitability* because:
1. 28% of rows are missing a `yes_best_ask`. 
2. Volume/size fields (`ask_size`, `bid_size`) are entirely absent, making liquidity capacity assumptions impossible to validate.
3. Over 4,000 rows have spreads > 0.06, which would fail live execution policies.

**Which conclusions are supported?**
- Strategy hit rates (win/loss ratios).
- Theoretical edge sizing before spread crossing.

**Which conclusions are not supported?**
- PnL (profitability).
- Capital allocation or expected return.
- Realistic execution frequencies (we don't know if the ask size was $0.10 or $1,000).

**Which fields are missing for real execution analysis?**
- `yes_ask_size`, `no_ask_size`
- `yes_bid_size`, `no_bid_size`
- Accurate `policy_allowed` tags within the parquet

**Which rows/matches must be repaired before rerunning profitability?**
- Matches identified in `settlement_coverage.csv` missing terminal outcome labels.
- Rows with missing `yes_best_ask` or `yes_best_bid` must be dropped or forward-filled (with strict age caps) before running backtests.
- Rows with negative book ages must be dropped.

**Should repaired data replace the original parquet or be written as a derived dataset?**
Write as a derived dataset (`data_v2/model_value_replay_repaired.parquet`). Do NOT mutate the raw `.parquet` to preserve auditability.

**What is the exact next script to run?**
A dataset repair/backfill script (e.g. `scripts/repair_replay_dataset.py`) to merge opendota outcomes, drop/impute missing books, and output the repaired parquet.
