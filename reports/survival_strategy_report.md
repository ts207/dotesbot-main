# Survival Strategy Report

Generated: 2026-06-07T20:35:34+00:00
Profile: `survival_strategy_v1`
Decision: **PAPER_OR_OBSERVE**

## Strategy

Primary: VALUE hold-to-settlement on GetTopLive top_live snapshots. Back the net-worth leader only when fair-price edge survives the validated gates.

Secondary: DSWING BO3 moneyline convergence after a decisive map swing. Keep off by default because the sample is small and exit liquidity is thin.

Rejected branch: Model B residual/pregame feature trading. Current checked-in diagnosis says B0 market-only remains best and residual variants failed source-robust validation.

## Evidence

VALUE no-confirmation replay: trades=18 wins=14 losses=4 roi=23.2% pnl=$83.58.
DSWING best checked sweep: lead>=6000 trades=15 win_pct=67.0% roi=40.3%.
GetTopLive structure audit: rows=284 matches=5 valid_tower_deltas=12 building_only_changes=16 tower_count_increases=2.
VALUE survival feature audit: join_coverage=83.3% live_gate_change=none candidate_observations=5.

## GetTopLive State Audit

Status: `pass`
Recent rows read: 2000 from `\\wsl.localhost\Ubuntu\home\tstuv\dota-poly-signal-pnl-asd\logs\raw_snapshots.csv`
Required source `top_live` rows: 7 across 2 matches.
Source counts: {'live_league': 1993, 'top_live': 7}
Missing required fields: []
Missing recommended fields: []

## Structure State Policy

- TopLive building_state: required_for_audit_and_lane_tower_decode
- TopLive tower_state: decoded_from_validated_lane_tower_progress_bits
- TopLive rax/T4/base: research_only_until_layout_validated
- Runtime marker: `building_state_schema=top_live_lane_tower_progress`
- Survival rule: Do not generate rax/base-pressure trades from raw GetTopLive building_state.

## Current Config Audit

- INFO real_live_disabled: Real live trading is disabled; strategy can observe or paper trade without capital risk.
- WARN value_trading_off: VALUE entries are disabled. The primary strategy will only log rejects/signals.
- WARN trade_size_high_for_nav: MAX_TRADE_USD=$20 is aggressive versus latest monitor NAV $48.85.
- WARN value_cap_high_for_nav: VALUE_MAX_PER_MATCH=$20 is aggressive versus latest monitor NAV $48.85.
- WARN total_live_cap_high_for_nav: MAX_TOTAL_LIVE_USD=$200 exceeds 35% of latest monitor NAV $48.85.
- INFO event_detectors_off: Event detector trading is off; this avoids the fragile short-horizon/momentum paths.
- INFO dswing_off: DSWING is off; keep it off until explicitly armed with capital and series-state checks.
- WARN stale_position_needs_redeem: !! WARN: 1 position(s) with no book (settled/illiquid - may need redeem): Dota 2: Aurora vs Team Yandex - Game(77sh)

## Go-Live Rules

- Do not enable real trading from this report alone.
- Before real capital: stable balance reads, no stale no-book position, bot-only commitment, and caps scaled to bankroll.
- Keep VALUE confirmation off unless a fresh replay proves otherwise.
- Keep Model B out of trading until it passes source-robust validation.
