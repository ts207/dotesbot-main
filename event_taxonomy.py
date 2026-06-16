from __future__ import annotations

EVENT_SCHEMA_VERSION = "cadence_v1"

TIER_A_EVENTS = frozenset({
    # POLL_ULTRA_LATE_FIGHT_FLIP demoted 2026-05-26 → RESEARCH (signed_60=0c on n=6).
    "POLL_BUYBACK_CAPITULATION",
})

# Premium sub-signals — same event but with feature thresholds that lift EV ~3x.
# Backtest: n=21 trades at $50 = +$13/trade, 71% win, max DD $5 (1%).
# Format: (event_type, feature_name, threshold) -> require feature_value >= threshold.
# Used by signal_engine to apply a 2x sizing multiplier when the trigger qualifies.
PREMIUM_EVENT_FILTERS: dict[str, tuple[str, float]] = {
    "POLL_LATE_FIGHT_FLIP": ("event_confidence", 0.90),       # +13.7c, 80% (n=5)
    "POLL_VALUE_DISAGREEMENT": ("networth_delta_abs", 2000),  # +8.3c, 67% (n=9)
    "POLL_KILL_BURST_CONFIRMED": ("networth_delta_abs", 5000), # +3.4c, 83% (n=12)
}

TIER_B_EVENTS = frozenset({
    # Promoted 2026-05-26 (whitelist rebuild from full 1059-event signal-quality audit).
    # All survivors have signed_60>=+0.7c on >=14 samples.
    "OBJECTIVE_CONVERSION_T2",      # n=14 +2.28c 64% win
    "POLL_LATE_FIGHT_FLIP",         # n=13 +4.87c 62% win — premium-tier when confidence>=0.9
    "POLL_VALUE_DISAGREEMENT",      # n=87 +2.87c 56% — workhorse (high freq, hold-to-settle)
    "POLL_STRUCTURAL_DOMINANCE",    # n=79 +0.71c 65% — decided-game drift
    "POLL_KILL_BURST_CONFIRMED",    # n=60 +2.09c 70% — NEW; +9.86c on big-move subset
    "POLL_FIGHT_SWING",             # 2026-05-29 reaffirmed: n=17 buys, 82% win at settle, +$4.57
    # 2026-05-29 promoted from RESEARCH after settlement audit. The earlier
    # demotions were based on 30s/60s markout numbers that ignored settlement
    # payoff. At game_over these events are all consistent winners:
    "POLL_RAPID_STOMP",             # n=18 buys, 83% win settle, +$5.52
    "POLL_DECISIVE_STOMP",          # n=8 buys, 88% win settle, +$2.00
    "POLL_LEAD_FLIP_WITH_KILLS",    # n=8 buys, 75% win settle, +$4.80
    "POLL_ULTRA_LATE_FIGHT_FLIP",   # n=1 buy, settled +$2.03 (small sample, monitor)
    # 2026-05-29 — new detector POLL_PRE_PUSH_SETUP from data discovery:
    # fires when one side has 3+ enemy towers down + 5k+ nw lead + gt>=25min,
    # regardless of kill score. 12-day backtest: 375 fires, 91% settle win,
    # +$44 sum_pset. Catches games where market under-prices the structural
    # leader because kill score looks even. Distinct from STRUCTURAL_DOMINANCE
    # which requires kill_lead>=2.
    "POLL_PRE_PUSH_SETUP",
    # 2026-05-30 #6 — true NW/kill divergence detector. When farm leads but
    # kills trail (or vice versa), 7d backfill at (NW>=3000, kill>=3 opposite)
    # shows 76% wr on n=45 matches with NW-favored side winning. This is the
    # signal POLL_VALUE_DISAGREEMENT was named for but didn't implement.
    "POLL_NW_KILL_DIVERGENCE",
    # 2026-05-30 Phase B — real-time-only detectors. GetTopLiveGame gives
    # game_time + scores + radiant_lead as undelayed data; these detectors
    # use ONLY those fields, so they fire before MM sees the state change
    # via delayed GetRealtimeStats or broadcast feed.
    "POLL_KILL_BURST_TIGHT",
    "POLL_NW_VELOCITY_SUSTAINED",
    "POLL_KILL_GAP_ACCEL",
    "POLL_PHASE_NORMALIZED_LEAD",
    # 2026-05-30 — FADE variant of POLL_MAJOR_COMEBACK_RECOVERY. The original
    # event tracks a team "recovering" from a deficit, but real settle data
    # (n=53, 7d) shows the recovering team wins only 34% — i.e. the comeback
    # is mostly noise and the leader still wins. Betting AGAINST the recovery
    # → 66% wr. Cap set to 0.66 = break-even, EV positive below 0.66.
    "POLL_MAJOR_COMEBACK_FADE",
})

# Demoted 2026-05-25 from TIER_A / TIER_B → not in TRADE_EVENTS by default.
# In pro Dota, teams `gg` (concede) before T3/T4/throne falls, so these events
# essentially never fire: 101 captured matches (89 reached >30 min), only 2
# showed any T3 fall; zero fires for T4, RAX, THRONE_EXPOSED, BASE_PRESSURE_*
# across all of live operation (`logs/dota_events.csv`). Kept defined so they
# can be re-promoted if the bot moves to a league with different end-game culture.
UNREACHABLE_PRO_EVENTS = frozenset({
    "OBJECTIVE_CONVERSION_T4",
    "OBJECTIVE_CONVERSION_RAX",
    "OBJECTIVE_CONVERSION_T3",
    "THRONE_EXPOSED",
    "BASE_PRESSURE_T4",
    "BASE_PRESSURE_T3_COLLAPSE",
})

TIER_C_EVENTS = frozenset()

RESEARCH_EVENTS = frozenset({
    # Demoted from TIER_B 2026-05-26 — B4 backtest n=3 mean=−0.77 win=33%.
    "POLL_TEAM_WIPE",
    "BLOODY_EVEN_FIGHT",         # signed_60=-1.73c 33% win (n=24) — anti-signal
    "ECON_ONLY_MOVE",
    "STRUCTURE_CONTEXT",
    "LOW_PRICE_UNDERDOG_COUNTERPUNCH",
    "LATE_CHEAP_LEAD_SWING_REPRICE",
    "CORE_NETWORTH_CRASH",
    "CORE_GAP_FLIP",
    "SUPPORT_KILL_FILTER",
    "AEGIS_PUSH_WINDOW",
    "ROSHAN_SWING",
    # Signal-quality audit 2026-05-26 confirmed anti-signal — DO NOT TRADE
    "POLL_STOMP_THROW_CONFIRMED",      # 2026-05-29 settle audit: 2 trades, 0% win, -$2.00 — confirmed kill
    "POLL_MAJOR_COMEBACK_RECOVERY",    # 2026-05-29 settle audit: 2 trades, 0% win, -$2.00 — confirmed kill
    # 2026-05-29: POLL_COMEBACK_RECOVERY demoted from TIER_B. Settle audit on
    # 3 paper buys: 0% win, -$3.00 sum. The 20-trade pre-deploy backtest claim
    # of 75% win didn't hold in production. Re-evaluate after more samples.
    "POLL_COMEBACK_RECOVERY",
    # POLL_LEAD_FLIP_WITH_KILLS, POLL_RAPID_STOMP, POLL_ULTRA_LATE_FIGHT_FLIP,
    # POLL_DECISIVE_STOMP — moved to TIER_B per same settle audit (positive at game_over).
})

BLOCKING_EVENTS = frozenset({
    "PRICED_OUT_HIGH_GROUND_STOMP",
    "WIDE_SPREAD_COMEBACK_ALERT",
    "STALE_BOOK_STRONG_EVENT",
    "STALE_SOURCE_EVENT",
    "CHASING_TERMINAL_PRICE",
    "MAPPING_UNCERTAIN_EVENT",
    "DUPLICATE_MATCH_MAPPING_EVENT",
})

RETIRED_FIXED_WINDOW_EVENTS = frozenset({
    "LEAD_SWING_30S",
    "LEAD_SWING_60S",
    "EXTREME_LEAD_SWING_30S",
    "TEAMFIGHT_SWING_30S",
    "KILL_BURST_30S",
    "KILL_CONFIRMED_LEAD_SWING",
    "FIGHT_TO_GOLD_CONFIRM_30S",
    "LATE_GAME_WIPE",
    "ULTRA_LATE_WIPE",
    "STOMP_THROW",
    "COMEBACK",
    "COMEBACK_RECOVERY_60S",
    "MAJOR_COMEBACK",
    "MAJOR_COMEBACK_RECOVERY_60S",
    "LATE_MAJOR_COMEBACK_REPRICE",
    "CHAINED_LATE_FIGHT_RECOVERY",
    "LATE_ECONOMIC_CRASH",
    "ULTRA_LATE_WIPE_CONFIRMED",
    "STOMP_THROW_WITH_OBJECTIVE_RISK",
    "T2_TOWER_FALL",
    "T3_TOWER_FALL",
    "MULTIPLE_T2_TOWERS_DOWN",
    "ALL_T2_TOWERS_DOWN",
    "MULTIPLE_T3_TOWERS_DOWN",
    "ALL_T3_TOWERS_DOWN",
    "FIRST_T4_TOWER_FALL",
    "SECOND_T4_TOWER_FALL",
    "T3_PLUS_T4_CHAIN",
    "MULTI_STRUCTURE_COLLAPSE",
    "BLOODY_EVEN_FIGHT_30S",
})

FIRST_LIVE_ALLOWLIST = frozenset({
    "THRONE_EXPOSED",
    "OBJECTIVE_CONVERSION_T4",
    "POLL_ULTRA_LATE_FIGHT_FLIP",
})

EVENT_FAMILY: dict[str, str] = {
    "OBJECTIVE_CONVERSION_T4": "fight_objective_conversion",
    "OBJECTIVE_CONVERSION_RAX": "fight_objective_conversion",
    "OBJECTIVE_CONVERSION_T3": "fight_objective_conversion",
    "OBJECTIVE_CONVERSION_T2": "fight_objective_conversion",
    "THRONE_EXPOSED": "terminal_base",
    "BASE_PRESSURE_T4": "terminal_base",
    "BASE_PRESSURE_T3_COLLAPSE": "base_pressure",
    "POLL_ULTRA_LATE_FIGHT_FLIP": "late_reversal",
    "POLL_BUYBACK_CAPITULATION": "late_reversal",
    "POLL_STOMP_THROW_CONFIRMED": "late_reversal",
    "POLL_LATE_FIGHT_FLIP": "late_reversal",
    "POLL_LEAD_FLIP_WITH_KILLS": "late_reversal",
    "POLL_MAJOR_COMEBACK_RECOVERY": "late_reversal",
    "POLL_MAJOR_COMEBACK_FADE": "late_reversal_fade",
    "POLL_COMEBACK_RECOVERY": "late_reversal",
    "POLL_KILL_BURST_CONFIRMED": "fight_economy_confirmation",
    "POLL_FIGHT_SWING": "fight_economy_confirmation",
    "POLL_VALUE_DISAGREEMENT": "value_disagreement",
    "POLL_NW_KILL_DIVERGENCE": "value_disagreement",
    "POLL_DECISIVE_STOMP": "base_pressure",
    "POLL_RAPID_STOMP": "base_pressure",
    "POLL_STRUCTURAL_DOMINANCE": "decided_game",
    "BLOODY_EVEN_FIGHT": "teamfight_context",
    "ECON_ONLY_MOVE": "research",
    "STRUCTURE_CONTEXT": "research",
}


def event_tier(event_type: str | None) -> str:
    if event_type in TIER_A_EVENTS:
        return "A"
    if event_type in TIER_B_EVENTS:
        return "B"
    if event_type in TIER_C_EVENTS:
        return "C"
    if event_type in RESEARCH_EVENTS:
        return "research"
    if event_type in BLOCKING_EVENTS:
        return "block"
    if event_type in RETIRED_FIXED_WINDOW_EVENTS:
        return "retired"
    if event_type in UNREACHABLE_PRO_EVENTS:
        return "unreachable_pro"
    return "unknown"


def event_is_primary(event_type: str | None) -> bool:
    return event_tier(event_type) in {"A", "B"}


def event_family(event_type: str | None) -> str:
    if event_type in BLOCKING_EVENTS:
        return "blocking"
    if event_type in RESEARCH_EVENTS:
        return "research"
    if event_type in RETIRED_FIXED_WINDOW_EVENTS:
        return "retired"
    return EVENT_FAMILY.get(str(event_type or ""), "unknown")


def first_live_allowed(event_type: str | None) -> bool:
    return event_type in FIRST_LIVE_ALLOWLIST
