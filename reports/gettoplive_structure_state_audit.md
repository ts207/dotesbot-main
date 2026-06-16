# GetTopLive Structure State Audit

Generated: 2026-06-07T20:13:28+00:00
Input: `\\wsl.localhost\Ubuntu\home\tstuv\dota-poly-signal-pnl-asd\logs\raw_snapshots.csv` tail_rows=20000

## Totals

- top_live_rows: 284
- top_live_matches: 5
- building_state_changes: 52
- tower_state_changes: 36
- building_change_without_tower_change: 16
- valid_tower_deltas: 12
- tower_count_increases: 2

## Interpretation

- Tradeable now: decoded lane-tower transitions only
- Research only: raw TopLive building_state rax/base/T4 interpretation
- Survival rule: Do not trade rax/base pressure from raw TopLive building_state.

## Examples

- match_id=8842596730 game_time=1373 building 9568402->9633938 tower=4044726
- match_id=8842596730 game_time=1472 building 9633938->13828242 tower=4044726
- match_id=8842596730 game_time=1623 building 13828242->13828250 tower=4044726
- match_id=8842596730 game_time=1851 building 13828250->13828314 tower=4044726
- match_id=8842596730 game_time=1911 building 13828314->13828315 tower=4044726
- match_id=8842596730 game_time=2527 building 13828388->13828900 tower=4044580
- match_id=8842722527 game_time=1975 building 9633938->9634002 tower=4044726
- match_id=8842722527 game_time=2100 building 9634002->9634010 tower=4044726
- match_id=8842722527 game_time=2858 building 9634010->9634011 tower=4044726
- match_id=8842807923 game_time=2643 building 29229274->29294810 tower=3745718
