# Market Disagreement Regime Alpha Audit

## Universe
- Total Polls Analyzed: 2459

## Candidate Rule Families

### VALUE_v1_baseline
- diagnostic_only: True
- policy_frozen: True
- signals: 63
- fills: 19
- unique_matches: 19
- unique_episodes: 19
- settlement_pnl: 97.478
- pnl_30s: 0.000
- pnl_60s: 0.000
- pnl_300s: 0.000
- convergence_pnl: 97.478
- ROI: 0.257
- win_rate: 0.789
- max_drawdown: 40.000
- avg_ask: 0.650
- avg_fair: 0.830
- avg_edge: 0.180
- avg_spread: 0.058
- avg_book_age_ms: 3076.868
- top_1_trade_share: 0.078
- top_3_trade_share: 0.233
- top_5_trade_share: 0.374
- sample_size_warning: LOW_SAMPLE_SIZE
- concentration_warning: LOW_SAMPLE_SIZE
- causality_violations: 0
- overlap_with_value_v1: 19
- incremental_trades_vs_value_v1: 0
- incremental_pnl_vs_value_v1: 0.000

### 1_market_veto_override
- diagnostic_only: True
- policy_frozen: True
- signals: 32
- fills: 16
- unique_matches: 16
- unique_episodes: 16
- settlement_pnl: -14.881
- pnl_30s: 0.000
- pnl_60s: 0.000
- pnl_300s: 0.000
- convergence_pnl: -14.881
- ROI: -0.047
- win_rate: 0.688
- max_drawdown: 59.984
- avg_ask: 0.719
- avg_fair: 0.843
- avg_edge: 0.124
- avg_spread: 0.019
- avg_book_age_ms: 1963.852
- top_1_trade_share: 0.108
- top_3_trade_share: 0.324
- top_5_trade_share: 0.540
- sample_size_warning: LOW_SAMPLE_SIZE
- concentration_warning: LOW_SAMPLE_SIZE
- causality_violations: 0
- overlap_with_value_v1: 10
- incremental_trades_vs_value_v1: 6
- incremental_pnl_vs_value_v1: 21.332

### 2_cheap_leader_trap
- diagnostic_only: True
- policy_frozen: True
- signals: 26
- fills: 6
- unique_matches: 6
- unique_episodes: 6
- settlement_pnl: 100.011
- pnl_30s: 0.000
- pnl_60s: 0.000
- pnl_300s: 0.000
- convergence_pnl: 100.011
- ROI: 0.833
- win_rate: 0.833
- max_drawdown: 20.000
- avg_ask: 0.475
- avg_fair: 0.795
- avg_edge: 0.320
- avg_spread: 0.063
- avg_book_age_ms: 1089.176
- top_1_trade_share: 0.214
- top_3_trade_share: 0.609
- top_5_trade_share: 0.878
- sample_size_warning: LOW_SAMPLE_SIZE
- concentration_warning: LOW_SAMPLE_SIZE
- causality_violations: 0
- overlap_with_value_v1: 2
- incremental_trades_vs_value_v1: 4
- incremental_pnl_vs_value_v1: 54.656

### 3_late_game_toxicity
- diagnostic_only: True
- policy_frozen: True
- signals: 21
- fills: 10
- unique_matches: 10
- unique_episodes: 10
- settlement_pnl: 6.670
- pnl_30s: 0.000
- pnl_60s: 0.000
- pnl_300s: 0.000
- convergence_pnl: 6.670
- ROI: 0.033
- win_rate: 0.600
- max_drawdown: 40.000
- avg_ask: 0.629
- avg_fair: 0.859
- avg_edge: 0.230
- avg_spread: 0.128
- avg_book_age_ms: 3446.911
- top_1_trade_share: 0.159
- top_3_trade_share: 0.399
- top_5_trade_share: 0.639
- sample_size_warning: LOW_SAMPLE_SIZE
- concentration_warning: LOW_SAMPLE_SIZE
- causality_violations: 0
- overlap_with_value_v1: 4
- incremental_trades_vs_value_v1: 6
- incremental_pnl_vs_value_v1: -11.798

### 4_stable_leader_continuation
- diagnostic_only: True
- policy_frozen: True
- signals: 241
- fills: 48
- unique_matches: 48
- unique_episodes: 48
- settlement_pnl: -17.782
- pnl_30s: 0.000
- pnl_60s: 0.000
- pnl_300s: 0.000
- convergence_pnl: -17.782
- ROI: -0.019
- win_rate: 0.729
- max_drawdown: 89.594
- avg_ask: 0.733
- avg_fair: 0.797
- avg_edge: 0.065
- avg_spread: 0.052
- avg_book_age_ms: 3376.678
- top_1_trade_share: 0.040
- top_3_trade_share: 0.119
- top_5_trade_share: 0.199
- sample_size_warning: 
- concentration_warning: 
- causality_violations: 0
- overlap_with_value_v1: 19
- incremental_trades_vs_value_v1: 29
- incremental_pnl_vs_value_v1: -23.647

### 5_market_lag_candidate
- diagnostic_only: True
- policy_frozen: True
- signals: 39
- fills: 29
- unique_matches: 29
- unique_episodes: 29
- settlement_pnl: 39.700
- pnl_30s: 0.000
- pnl_60s: 0.000
- pnl_300s: 0.000
- convergence_pnl: 39.700
- ROI: 0.068
- win_rate: 0.793
- max_drawdown: 94.655
- avg_ask: 0.779
- avg_fair: 0.813
- avg_edge: 0.034
- avg_spread: 0.018
- avg_book_age_ms: 1642.317
- top_1_trade_share: 0.099
- top_3_trade_share: 0.261
- top_5_trade_share: 0.404
- sample_size_warning: 
- concentration_warning: 
- causality_violations: 0
- overlap_with_value_v1: 8
- incremental_trades_vs_value_v1: 21
- incremental_pnl_vs_value_v1: 25.552

## Alpha Hierarchy Status
1. **VALUE v1**: frozen primary policy, shadow-forward only
2. **market_disagreement_regime_alpha**: research branch only (diagnostic_only=true)
3. **survival overlay**: observe-only feature logging
4. **transition/event**: diagnostic only
5. **DSWING**: separate explicitly armed branch, not part of VALUE alpha
6. **Model B**: rejected