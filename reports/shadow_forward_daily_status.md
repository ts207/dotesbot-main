# Shadow Forward Daily Status: 2026-06-15

```text
Usable validation days so far: 3
Calendar days observed: 4
Last usable validation day: 2026-06-15
Current reject reason: none
```

## 1. Daily Metrics

```json
{
  "date": "2026-06-15",
  "feed_active_hours": 0.01,
  "VALUE_evaluations": 10,
  "VALUE_WOULD_ENTER_count": 0,
  "VALUE_reject_distribution": {
    "game_too_early": 10
  },
  "near_threshold_pressure_score": 0.017,
  "deep_discount_candidate_count": 0,
  "market_lag_candidate_count": 0,
  "causality_violations": 0,
  "stale_book_WOULD_ENTER_count": 0,
  "degraded_source_WOULD_ENTER_count": 0,
  "config_drift_detected": false,
  "decision": "CONTINUE",
  "validation_day_usable": true,
  "validation_day_reject_reason": null,
  "future_book_relative_to_snapshot_count": 0,
  "future_book_relative_to_snapshot_would_enter_count": 0,
  "negative_book_age_count": 0,
  "negative_book_age_would_enter_count": 0,
  "max_negative_book_age_ms": 0,
  "excessive_skew_count": 0,
  "excessive_skew_would_enter_count": 0,
  "validation_eligible_false_detected": false,
  "backlog_replay_mode_detected": false,
  "monitor_process_down": false,
  "affected_monitor": ""
}
```

## 2. Health Checker Output

```text
SHADOW_HEALTH=NO_GO
SHADOW_STATE=WAITING_FOR_MARKET_DATA
VALIDATION_CLOCK=NOT_STARTED
SAFETY_STATUS=PASS
DATA_STATUS=WAITING_FOR_MARKET_DATA

Reasons:
 - primary VALUE log is stale (7287.2 mins old)
 - secondary alpha log is stale (7287.2 mins old)

```

## 3. Rejection Diagnostics

```text
============================================================
 VALUE ENGINE REJECTION ANALYSIS 
============================================================
Total Evaluations: 17916
Unique Matches: 54

--- Rejection Reason Distribution ---
reject_reason
game_too_early                          4137
price_too_high                          3787
series_market_unpriced                  2520
edge_too_small                          1642
lead_too_small                          1491
game_too_late                           1120
fair_too_low                            1025
missing_ask                              753
price_too_low: cheap_reject_broad        639
edge_too_large                           173
missing_top_live_state:game_time_sec      78
missing_game_time                         70
book_stale                                 7

--- Edge Too Small Bucketing ---
edge_bucket
0.000 - 0.005 below threshold      19
0.005 - 0.010 below threshold       9
0.010 - 0.025 below threshold      46
0.025 - 0.050 below threshold      99
>0.050 below threshold           1469

--- Match-Level Edge Stats ---
  match_id  max_edge  closest_edge_miss_per_match
8836624153  0.111686                     0.070231
8836641131 -0.012718                     0.162718
8836692392  0.031253                     0.118747
8836750303       NaN                          NaN
8836784177  0.190000                     0.072952
8836793131  0.379862                     0.059872
8836846548       NaN                          NaN
8836872602  0.017893                     0.132107
8836897842  0.271086                     0.056775
8836916511  0.140400                     0.050910
8836936103       NaN                          NaN
8837016390       NaN                          NaN
8837019318       NaN                          NaN
8837052943  0.237489                     0.200907
8837134119       NaN                          NaN
8837192214  0.033057                     0.116943
8837288943  0.047256                     0.102744
8837463516       NaN                          NaN
8837519479 -0.006115                     0.156115
8837560468  0.009351                     0.140649
8837603031 -0.002388                     0.152388
8837690631  0.137378                     0.050162
8837692542 -0.096248                          NaN
8837724891 -0.015976                          NaN
8837725916  0.061009                     0.106557
8837759074  0.147007                     0.062993
8837827200  0.051165                     0.098835
8837869969  0.137975                     0.082025
8837889126  0.020470                     0.155774
8838480778       NaN                          NaN
8839123109  0.232192                     0.000107
8839193447  0.020353                     0.129647
8839206225       NaN                          NaN
8839281131       NaN                          NaN
8839284303 -0.067804                          NaN
8839366504       NaN                          NaN
8842134886       NaN                          NaN
8842245726  0.297875                     0.001230
8842468101  0.350000                     0.003469
8842596730  0.320000                     0.076485
8842807923 -0.056057                          NaN
8842905182 -0.043466                     0.193466
8843465364  0.076472                     0.130441
8843512570  0.095492                     0.079714
8843560379  0.144485                     0.005515
8843636057  0.119831                     0.107066
8843760302       NaN                          NaN
8843915671 -0.173011                          NaN
8844054970       NaN                          NaN
8844132483  0.130879                     0.019121
8844244719  0.247573                     0.001643
8844308689 -0.004181                     0.154181
8852543650       NaN                          NaN
8852555586       NaN                          NaN

--- Near Miss Price Corrections ---
                   timestamp_utc   match_id     edge  ask  edge_shortfall  seconds_until_price_over_0_84_after_near_miss
2026-06-05 09:47:24.936000+00:00 8839123109 0.143103 0.69        0.006897                                        406.329
2026-06-05 09:51:45.538000+00:00 8839123109 0.145803 0.67        0.004197                                        145.727
2026-06-05 09:53:15.021000+00:00 8839123109 0.142192 0.68        0.007808                                         56.244
2026-06-05 09:53:19.739000+00:00 8839123109 0.142192 0.68        0.007808                                         51.526
2026-06-08 11:41:35.930000+00:00 8843560379 0.144485 0.60        0.005515                                         98.365
2026-06-08 11:41:39.946000+00:00 8843560379 0.144485 0.60        0.005515                                         94.349
2026-06-08 20:03:56.736000+00:00 8844244719 0.148357 0.74        0.001643                                        836.380
2026-06-08 20:09:29.242000+00:00 8844244719 0.145884 0.70        0.004116                                        503.874
2026-06-08 20:09:36.790000+00:00 8844244719 0.145884 0.70        0.004116                                        496.326
2026-06-08 20:09:44.735000+00:00 8844244719 0.145884 0.70        0.004116                                        488.381
2026-06-08 20:09:53.239000+00:00 8844244719 0.145884 0.70        0.004116                                        479.877
2026-06-08 20:10:02.110000+00:00 8844244719 0.145884 0.70        0.004116                                        471.006
2026-06-08 20:10:11.080000+00:00 8844244719 0.143487 0.70        0.006513                                        462.036

```

## 4. VALUE v1 Shadow Results

```text
# Value v1 Shadow-Forward Summary
    
## Monitoring Status
- **Generated At**: 2026-06-15T04:26:48.568023+00:00
- **Total Polls Evaluated**: 12971
- **Unique Matches**: 127
- **Causality Violations**: 0

## Decision Breakdown
- **WOULD_ENTER**: 0
- **Unique Episodes**: 0
- **Avg Signals/Episode**: 0
- **WOULD_SKIP**: 4443
- **WOULD_REJECT**: 8528
- **DUPLICATE_BLOCKED**: 0

## Reject Reasons
- **missing_book**: 3991
- **series_market_unpriced**: 3608
- **game_too_early**: 3013
- **game_too_late**: 1386
- **missing_ask**: 630
- **game_over**: 189
- **book_stale**: 84
- **missing_snapshot_timestamp**: 70

## PnL Metrics
- **Episodes Scored**: 0
- **Episodes Settled**: 0
- **Total Settled PnL**: 0.0
- **Avg PnL 30s**: 0.0
- **Avg PnL 60s**: 0.0
- **Avg PnL 300s**: 0.0
- **Avg PnL to Convergence**: 0.0

## Operational Readiness Review
- Continuous Monitoring: IN_PROGRESS
- Zero Causality Violations: PASS
- Validated Baseline Reconciliation: PENDING_FORWARD_DATA


```

## 5. Alpha Shadow Results

```text
Error running scripts/analyze_market_disagreement_alpha_shadow.py: Command '['python3', 'scripts/analyze_market_disagreement_alpha_shadow.py']' timed out after 30 seconds
```

