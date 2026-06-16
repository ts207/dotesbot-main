# Net-Worth Policy Comparison Summary
    
## Reconciliation
- Value Replay Reconciliation Pass: True
- Expected Trades: 18
- Observed Trades: 18
- Expected PnL: $83.58
- Observed PnL: $83.58

## Key Findings
- **Best Current Policy**: level_value_hold (Validated PnL: $83.58)
- **Does Transition Add Value?**: See overlap audit. Level only: 50, Transition only: 0.
- **Do Events Miss Opportunities?**: 194 highly profitable net-worth polls had no signal.
- **Hold vs Quick Exit**: Convergence PnL vs 30s/60s PnL (Check CSV for exact metrics).
- **Concentration/Sample Size**: Check `sample_size_warning` in comparison CSV.

## Next Action
Review the quadrant distribution and overlap metrics to determine if the `transition_only` signals justify arming an independent secondary transition policy, or if `level_value_hold` with convergence exits fully saturates the observable net-worth edge.
