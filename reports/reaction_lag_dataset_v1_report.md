# Reaction Lag Dataset V1

- Input rows: 1089
- Input matches: 42
- Output rows: 1089
- Output matches: 42
- Candidate rows, lag >= 0.02: 218
- Candidate matches, lag >= 0.02: 33

## Feature Columns
- state_price_equiv_30s
- state_price_equiv_60s
- state_move_score
- price_response_score
- reaction_lag_score
- state_price_response_ratio
- wrong_way_or_flat_price

## Diagnostics
- avg_ask: 0.396540
- avg_state_move_score: -0.015817
- avg_price_response_score: -0.005172
- avg_reaction_lag_score: -0.010645
- avg_clv_120s: -0.021070
- avg_clv_300s: -0.029989

## Candidate Buckets
| lag_min | rows | matches | avg_clv_120s | avg_clv_300s |
|---:|---:|---:|---:|---:|
| 0.02 | 218 | 33 | -0.011165 | -0.013151 |
| 0.04 | 146 | 29 | -0.011767 | -0.009048 |
| 0.06 | 93 | 23 | -0.013737 | -0.018505 |
| 0.08 | 53 | 18 | -0.004689 | -0.010896 |
| 0.10 | 31 | 13 | -0.007048 | -0.013306 |
