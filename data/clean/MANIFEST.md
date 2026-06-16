# Clean Dataset — Dota/Polymarket S1 Strategy

Cleaned, normalized, and organized from `logs/rich_context.csv` (Steam snapshots,
290 raw cols) + `logs/book_events.csv` (Polymarket book) + `logs/opendota_outcomes.json`.

Generated 2026-06-01. Regenerate with the clean/normalize/organize script.

## Structure
```
data/clean/
├── matches.csv              ← master index, one row per match
├── snapshots/<match_id>.csv ← per-match normalized time series
└── MANIFEST.md              ← this file
```

## Cleaning applied
- Dropped rows with invalid game_time (gt<0 or gt>7200s)
- Deduped snapshots by (game_time, yes_score, no_score)
- Dropped matches with <3 snapshots
- Forward-filled book bid/ask within each match (last known price carries forward)
- All prices rounded to 4 decimals; scores/leads as ints; timestamps ISO-8601

## Normalization — everything is YES-perspective
Using `steam_side_mapping`: when `reversed`, radiant/dire are swapped so that
`yes_*` columns always describe the YES token's side. This makes every match
directly comparable regardless of which Steam side maps to YES.

## matches.csv columns
| column | meaning |
|--------|---------|
| match_id | Steam match id |
| yes_team / no_team | teams (YES = Polymarket outcome[0]) |
| league_id | Steam league id |
| market_type | MAP_WINNER (single game, validated) or MATCH_WINNER (series) |
| mapping | normal / reversed / (blank if unbound) |
| bound | 1 if a Polymarket binding exists |
| snapshots | # of cleaned snapshots |
| duration_min | last snapshot game-time |
| final_kills | yes_score-no_score at last snapshot |
| final_yes_nw | net-worth lead (YES perspective) at last snapshot |
| has_book | 1 if any Polymarket ask was captured |
| outcome_radiant_won | 1/0 from OpenDota, blank if unresolved |
| yes_won | outcome XOR mapping (1 = YES token settled to $1) |

Sorted: bound+book first, then by snapshot count.

## snapshots/<match_id>.csv columns
| column | meaning |
|--------|---------|
| timestamp_utc | ISO-8601 snapshot time |
| gt_sec / gt_min | game time |
| yes_score / no_score | kills (YES perspective) |
| yes_kill_lead | yes_score − no_score |
| yes_nw_lead | net-worth lead, + = YES ahead |
| yes_lead_per_min | nw_lead normalized by game minutes (phase-adjusted) |
| yes_bid / yes_ask / yes_spread | YES token book (forward-filled) |
| no_bid / no_ask / no_spread | NO token book (forward-filled) |

## Counts (this generation)
- 304 matches total
- 76 bound (Polymarket binding)
- 67 with book data (tradeable analysis set)
- 75 with settled outcome

## Usage
```python
import pandas as pd
idx = pd.read_csv('data/clean/matches.csv')
tradeable = idx[(idx.has_book==1) & (idx.outcome_radiant_won.notna())]
ts = pd.read_csv(f'data/clean/snapshots/{match_id}.csv')
# S1 backtest: first kill-coincident nw swing in gt 10-35, ask 0.45-0.85, hold to yes_won
```
