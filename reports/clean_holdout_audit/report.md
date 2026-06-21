# Clean Holdout Audit Report

This report covers the backtest on the 21 pure holdout matches (unseen during train and validation).
Production gates enforced: 420-2400s game time, spread <= 0.05, book_age_ms <= 5000, confirmation enabled, one trade per match.

## Threshold 0.01
- Trades: 19
- Settlement ROI: 14.19%
- CLV_900s: 0.0089
- CLV_1200s: 0.0338
- ROI excluding best 3 trades: -3.93%

### Model Output Clipping
- Total signals: 13736
- Clipped signals (0 or 1): 2222 (16.18%)
- Clipped trades: 0
- Unclipped trades: 19
- Unclipped trades ROI: 14.19%

### Net Worth Feature Missingness
- Trades WITH net worth lead: 19
- Trades WITHOUT net worth lead: 0
- WITH net worth ROI: 14.19%

## Threshold 0.02
- Trades: 19
- Settlement ROI: 21.83%
- CLV_900s: 0.0421
- CLV_1200s: 0.0696
- ROI excluding best 3 trades: 5.14%

### Model Output Clipping
- Total signals: 13866
- Clipped signals (0 or 1): 2222 (16.02%)
- Clipped trades: 0
- Unclipped trades: 19
- Unclipped trades ROI: 21.83%

### Net Worth Feature Missingness
- Trades WITH net worth lead: 19
- Trades WITHOUT net worth lead: 0
- WITH net worth ROI: 21.83%

## 0.01 vs 0.02 Paired Comparison on Holdout
- Common matches traded: 19
- Matches traded only in 0.01: 0
- Matches traded only in 0.02: 0
- Avg ask improvement (0.01 over 0.02): 0.0147
- Avg CLV_1200s delta (0.01 over 0.02): -0.0358