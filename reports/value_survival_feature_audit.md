# VALUE Survival Feature Audit

Generated: 2026-06-07T20:34:58+00:00
Join coverage: 83.3%

## Baseline

- trades=18 wins=14 losses=4 win_pct=77.8% roi=23.2% pnl=$83.58

## Joined TopLive Context

- trades=15 wins=13 losses=2 win_pct=86.7% roi=35.7% pnl=$107.22

## Buckets

- enemy_towers_down:<3: trades=4 win_pct=75.0% roi=28.8% pnl=$23.03
- enemy_towers_down:>=3: trades=11 win_pct=90.9% roi=38.3% pnl=$84.19
- join:missing: trades=3 win_pct=33.3% roi=-39.4% pnl=$-23.64
- leader_kills:ahead_or_tied: trades=14 win_pct=85.7% roi=33.9% pnl=$94.96
- leader_kills:behind: trades=1 win_pct=100.0% roi=61.3% pnl=$12.26
- leader_towers:ahead_or_tied: trades=15 win_pct=86.7% roi=35.7% pnl=$107.22
- own_towers_down:<3: trades=14 win_pct=85.7% roi=33.9% pnl=$94.96
- own_towers_down:>=3: trades=1 win_pct=100.0% roi=61.3% pnl=$12.26
- score_and_tower:aligned: trades=14 win_pct=85.7% roi=33.9% pnl=$94.96
- score_and_tower:not_aligned: trades=1 win_pct=100.0% roi=61.3% pnl=$12.26

## Recommendation

- Live gate change: none
- Reason: Sample is small and no score/structure subgroup is promoted automatically. Use as research evidence only unless a fresh replay expands support.
