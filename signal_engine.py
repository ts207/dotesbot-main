from __future__ import annotations

import os
import time
from math import exp, log
from collections import deque
from dataclasses import dataclass
from typing import Any, Iterable

from team_utils import norm_team
from event_taxonomy import TIER_A_EVENTS, TIER_B_EVENTS, event_family, event_is_primary, event_tier
from series_model import compute_bo3_match_p
from market_scope import is_game3_match_proxy, market_scope_metadata

from config import (
    MAX_STEAM_AGE_MS, MAX_SOURCE_UPDATE_AGE_SEC, REQUIRE_TOP_LIVE_FOR_SIGNALS,
    MAX_BOOK_AGE_MS, MAX_SPREAD, MAX_VOLATILITY_SPREAD, MAX_MOMENTUM_CHASE,
    MIN_LAG, MIN_EXECUTABLE_EDGE,
    PRICE_LOOKBACK_SEC, DEFAULT_MAX_FILL_PRICE,
    MIN_ASK_SIZE_USD, PAPER_TRADE_SIZE_USD, PAPER_SLIPPAGE_CENTS,
    ENABLE_MATCH_WINNER_GAME3_PROXY, ENABLE_MATCH_WINNER_TRADING,
    UNDERDOG_REVERSAL_EVENTS, UNDERDOG_REVERSAL_MAX_ENTRY, UNDERDOG_REVERSAL_MIN_ENTRY,
    UNDERDOG_REVERSAL_MIN_EDGE, UNDERDOG_REVERSAL_MIN_LAG,
    FIGHT_SWING_MAX_GAME_TIME_SEC,
    FIGHT_SWING_MIN_GAME_TIME_SEC,
    S3_ENABLED, S3_MIN_NW_LEAD, S3_MIN_EDGE, S3_MAX_PRICE, S3_ELO_ENABLED, S3_ELO_MARGIN,
)


@dataclass(frozen=True)
class EventSpec:
    base: float
    cap: float
    half_life_sec: float


# Final fast-API event model. Ancient/game-over is intentionally NOT here:
# game_over/Ancient state changes are terminal handlers, not probability signals.
ACTIVE_EVENTS: dict[str, EventSpec] = {
    # 2026-05-27 — RE-CALIBRATED from shadow_trades.csv realized markouts.
    # Old `base` values were 10-100x too high (claimed +12c, reality +0.015c).
    # New `base` = empirical avg realized markout (60s) on actual live paper trades.
    # Events with realized < 0 are set to base=0.01 so the model rejects them
    # via MIN_EXECUTABLE_EDGE rather than recommending big trades.
    "OBJECTIVE_CONVERSION_T4":   EventSpec(0.35, 0.70, 2.0),    # untraded — keep prior
    "OBJECTIVE_CONVERSION_RAX":  EventSpec(0.22, 0.45, 4.0),    # untraded
    "OBJECTIVE_CONVERSION_T3":   EventSpec(0.18, 0.38, 6.0),    # untraded
    "OBJECTIVE_CONVERSION_T2":   EventSpec(0.023, 0.10, 8.0),   # audit avg +2.3c

    "THRONE_EXPOSED":            EventSpec(0.35, 0.70, 1.5),    # untraded
    "BASE_PRESSURE_T4":          EventSpec(0.24, 0.50, 3.0),    # untraded
    "BASE_PRESSURE_T3_COLLAPSE": EventSpec(0.14, 0.32, 6.0),    # untraded

    "POLL_ULTRA_LATE_FIGHT_FLIP": EventSpec(0.01, 0.10, 4.0),   # n=1 -6c → distrust
    "POLL_BUYBACK_CAPITULATION":  EventSpec(0.20, 0.45, 4.0),   # untraded — kept high
    "POLL_AEGIS_MOMENTUM":       EventSpec(0.18, 0.40, 30.0),   # untraded
    "POLL_VALUE_DISAGREEMENT":   EventSpec(0.029, 0.10, 10.0),  # audit +2.87c
    "POLL_STRUCTURAL_DOMINANCE": EventSpec(0.007, 0.05, 12.0),  # audit +0.71c marginal
    "POLL_STOMP_THROW_CONFIRMED": EventSpec(0.01, 0.05, 8.0),   # audit -8.2c, near-zero base
    "POLL_LATE_FIGHT_FLIP":       EventSpec(0.049, 0.18, 5.0),  # audit +4.9c
    "POLL_LEAD_FLIP_WITH_KILLS":  EventSpec(0.01, 0.05, 7.0),   # audit -3c, distrust
    "POLL_MAJOR_COMEBACK_RECOVERY": EventSpec(0.01, 0.05, 10.0),# audit ~0c, distrust
    "POLL_KILL_BURST_CONFIRMED":  EventSpec(0.021, 0.08, 6.0),  # audit +2.1c
    "POLL_FIGHT_SWING":           EventSpec(0.006, 0.05, 6.0),  # audit +0.6c marginal
    "POLL_TEAM_WIPE":            EventSpec(0.01, 0.05, 5.0),    # untraded recently, distrust
    "POLL_DECISIVE_STOMP":        EventSpec(0.002, 0.05, 8.0),  # audit +0.2c flat
    "POLL_RAPID_STOMP":           EventSpec(0.01, 0.05, 6.0),   # audit -0.1c flat, distrust
    "POLL_COMEBACK_RECOVERY":     EventSpec(0.023, 0.08, 10.0), # audit +2.3c (live shadow!)

    # 2026-05-30 — newly-added events that were silently rejected as
    # event_type_inactive because they weren't in ACTIVE_EVENTS. Fixed now.
    # EventSpec.base = empirical settle-edge proxy; intentionally small so
    # the executable_edge gate is bypassed (these are all EXIT_HORIZON=0
    # hold-to-settle events — the cap is the actual EV check).
    "POLL_PRE_PUSH_SETUP":         EventSpec(0.01, 0.05, 10.0),
    "POLL_NW_KILL_DIVERGENCE":     EventSpec(0.01, 0.05, 10.0),
    "POLL_MAJOR_COMEBACK_FADE":    EventSpec(0.01, 0.05, 10.0),
    # 2026-05-30 Phase B real-time-only detectors
    "POLL_KILL_BURST_TIGHT":       EventSpec(0.01, 0.05, 8.0),
    "POLL_NW_VELOCITY_SUSTAINED":  EventSpec(0.01, 0.05, 8.0),
    "POLL_KILL_GAP_ACCEL":         EventSpec(0.01, 0.05, 8.0),
    "POLL_PHASE_NORMALIZED_LEAD":  EventSpec(0.01, 0.05, 10.0),
    # 2026-05-31 — First-swing-settle: one entry per match, direction gatekeeper.
    # Backtest: 46 matches, 80% match-level wr, +$0.084/trade at entry_px 0.45-0.90.
    "POLL_FIRST_SWING_SETTLE":     EventSpec(0.01, 0.05, 10.0),
    # 2026-05-31 — Reversal entry: buy underdog early in comeback arc.
    # Backtest: 10 matches, 100% wr, +$0.684/trade at entry_px 0.05-0.45.
    "POLL_REVERSAL_ENTRY":         EventSpec(0.01, 0.05, 10.0),
}

# 2026-06-01 — Set to all active events so the require_primary gate never emits
# "no_primary_event". The actual tradeable restriction to the OPTION 3 winner set
# is enforced by BLACKLISTED_EVENTS (filtered first, ~L525) + TRADE_EVENTS
# (executor) — see WINNER_TRADE_EVENTS below. Keeping this broad is harmless: only
# winner-set events survive the blacklist filter before this check runs.
PRIMARY_TRADE_EVENTS = set(ACTIVE_EVENTS)

SUPPRESSIONS: dict[str, set[str]] = {
    "OBJECTIVE_CONVERSION_T4": {"BASE_PRESSURE_T4", "BASE_PRESSURE_T3_COLLAPSE", "OBJECTIVE_CONVERSION_RAX", "OBJECTIVE_CONVERSION_T3", "OBJECTIVE_CONVERSION_T2"},
    "OBJECTIVE_CONVERSION_RAX": {"OBJECTIVE_CONVERSION_T3", "OBJECTIVE_CONVERSION_T2", "BASE_PRESSURE_T3_COLLAPSE"},
    "THRONE_EXPOSED": {"BASE_PRESSURE_T4", "BASE_PRESSURE_T3_COLLAPSE"},
    "POLL_BUYBACK_CAPITULATION": {"POLL_ULTRA_LATE_FIGHT_FLIP", "POLL_LATE_FIGHT_FLIP", "POLL_TEAM_WIPE", "POLL_FIGHT_SWING", "POLL_KILL_BURST_CONFIRMED"},
    "OBJECTIVE_CONVERSION_T3": {"BASE_PRESSURE_T3_COLLAPSE", "OBJECTIVE_CONVERSION_T2"},
    "POLL_ULTRA_LATE_FIGHT_FLIP": {"POLL_LATE_FIGHT_FLIP", "POLL_KILL_BURST_CONFIRMED", "POLL_FIGHT_SWING"},
    "POLL_STOMP_THROW_CONFIRMED": {"POLL_KILL_BURST_CONFIRMED", "POLL_FIGHT_SWING", "POLL_COMEBACK_RECOVERY"},
    "POLL_LATE_FIGHT_FLIP": {"POLL_KILL_BURST_CONFIRMED", "POLL_FIGHT_SWING"},
    "POLL_LEAD_FLIP_WITH_KILLS": {"POLL_KILL_BURST_CONFIRMED", "POLL_FIGHT_SWING", "POLL_COMEBACK_RECOVERY"},
    "POLL_MAJOR_COMEBACK_RECOVERY": {"POLL_COMEBACK_RECOVERY"},
    "POLL_TEAM_WIPE": {"POLL_FIGHT_SWING", "POLL_KILL_BURST_CONFIRMED"},
    "POLL_FIGHT_SWING": {"POLL_KILL_BURST_CONFIRMED"}, # fight swing is broader than burst
    "POLL_RAPID_STOMP": {"POLL_DECISIVE_STOMP", "POLL_FIGHT_SWING"},
    "POLL_DECISIVE_STOMP": {"POLL_FIGHT_SWING"},
}

# Event-specific safety rails. Real acceptance still comes from fair_price - ask;
# these caps only prevent chasing obviously expensive entries.
_EVENT_MAX_FILL: dict[str, float] = {
    "OBJECTIVE_CONVERSION_T4": 0.98,
    "OBJECTIVE_CONVERSION_RAX": 0.96,
    "OBJECTIVE_CONVERSION_T3": 0.93,
    # 2026-05-30 — 7d shadow backfill: 83% settle wr (n=6 small). Bumped 0.78→0.83.
    "OBJECTIVE_CONVERSION_T2": 0.83,
    "THRONE_EXPOSED": 0.97,
    "BASE_PRESSURE_T4": 0.92,
    "BASE_PRESSURE_T3_COLLAPSE": 0.86,
    "POLL_ULTRA_LATE_FIGHT_FLIP": 0.94,
    "POLL_BUYBACK_CAPITULATION": 0.95,
    "POLL_AEGIS_MOMENTUM": 0.94,
    # 2026-05-29 rejection audit: with EXIT_HORIZON_BY_EVENT=0, the binding
    # math becomes (settle_payoff − entry) / entry, NOT a 30s reprice. At 95%
    # settle win, entry at 0.92 has positive EV: 0.95×(0.087) − 0.05×0.92 = +3.7%.
    # Of the 40 trades rejected by the previous 0.75 cap, 100% settled positive
    # (+$6.45). Cap raised to admit them.
    # 2026-05-30 — recalibrated from full 7d event-fire backfill (n=573).
    # Settle wr 85% — tightened 0.90→0.85 to match.
    "POLL_VALUE_DISAGREEMENT": 0.85,
    # POLL_STRUCTURAL_DOMINANCE: same logic. 17 trades rejected ≥ 0.85, all
    # 100% settle wins (+$1.81). Raised to 0.93.
    # 2026-05-30 — recalibrated from full 7d event-fire backfill (n=654).
    # Settle wr 92% — slightly tighter than the shadow-only 94% read.
    "POLL_STRUCTURAL_DOMINANCE": 0.92,
    # POLL_PRE_PUSH_SETUP — 2026-05-29 new detector. Backtest: 91% settle win,
    # 95% win at ask>=0.80, also strong cheap-entry bucket (+$1.25/trade at <0.50).
    # Cap matches STRUCTURAL_DOMINANCE.
    # 2026-05-30 — recalibrated from 7d backfill (n=84). Settle wr 76% —
    # significantly LOWER than the pre-deploy projection of 91%. Tightened
    # 0.93→0.76 to reflect actual data. Largest correction in today's set.
    "POLL_PRE_PUSH_SETUP": 0.76,
    "POLL_STOMP_THROW_CONFIRMED": 0.87,   # raised 0.82→0.87: comeback events need room
    # B4 per-event analysis 2026-05-26: cap 0.65 blocked 59% of fires (44 seen,
    # 1 accepted). Original 0.65 was set under fixed-horizon assumption where
    # stop-out was a real risk; with hold-to-settle the floor is settlement.
    # Raised 0.65 → 0.85 to match the new exit posture.
    "POLL_LATE_FIGHT_FLIP": 0.85,
    "POLL_LEAD_FLIP_WITH_KILLS": 0.84,
    "POLL_MAJOR_COMEBACK_RECOVERY": 0.87, # replay: capped at 0.85 with ask filter
    # 2026-05-29 rejection audit: 20 trades rejected ≥ 0.84, all 100% settle
    # wins (+$1.75). Raised to 0.92 matching the VALUE_DISAGREEMENT logic.
    # 2026-05-30 — 7d backfill (n=7 small): 86% settle wr. Tightened 0.92→0.86.
    "POLL_KILL_BURST_CONFIRMED": 0.86,
    # 2026-05-30 — POLL_MAJOR_COMEBACK_FADE is the inverse direction of
    # POLL_MAJOR_COMEBACK_RECOVERY. 7d backfill (n=53) on the underlying event
    # showed the recovering team wins only 34%; fading → 66% break-even cap.
    "POLL_MAJOR_COMEBACK_FADE": 0.66,
    # 2026-05-30 #6 — POLL_NW_KILL_DIVERGENCE. Backfill: 76% wr at NW>=3k/kill>=3.
    "POLL_NW_KILL_DIVERGENCE": 0.76,
    # 2026-05-30 Phase B — new detectors with no historical wr data yet.
    # Caps set conservatively based on related-event wr; will recalibrate
    # after first 50-100 fires settle.
    "POLL_KILL_BURST_TIGHT":      0.80,   # tighter window than KILL_BURST_CONFIRMED (0.86)
    "POLL_NW_VELOCITY_SUSTAINED": 0.82,   # similar profile to RAPID_STOMP (0.92) — start tight
    "POLL_KILL_GAP_ACCEL":        0.80,   # snowball indicator — moderate confidence
    "POLL_PHASE_NORMALIZED_LEAD": 0.78,   # rate-based — start tight
    # 2026-05-30 (re-revised) — first revision was wrong (sample of 14 was
    # unrepresentative). Full 60-trade parquet replay shows 85% wr overall
    # and 92% wr in the 0.83-0.95 bucket. The shadow markouts look bad
    # (-4c at 60s) but settle wr is real. Set cap to 0.92 = wr at the
    # eligible price range.
    "POLL_FIGHT_SWING": 0.92,
    "POLL_TEAM_WIPE": 0.88,
    # 2026-05-29 settlement audit reversed the May 19 demotion. The events ARE
    # positive when held to game_over. Caps raised to admit more fires:
    # DECISIVE_STOMP previously had 363 candidates, only 8 accepted (2.2%);
    # the 8 accepted had 88% settle win, +$2.00. Raising the cap should grow
    # n materially. RAPID_STOMP similarly: 147 candidates, 18 accepted (12%),
    # 83% settle win on the accepts.
    # 2026-05-30 (revised) — replay against parquet history showed the
    # subset of fires where ask < 0.96 had only 84% settle wr (not 96%).
    # The "all fires" wr was 96% but the trade-eligible subset is harder
    # because those fires happen earlier when market is less certain.
    # Tightened 0.96 → 0.84 to break-even at the eligible subset's wr.
    "POLL_DECISIVE_STOMP": 0.84,
    "POLL_RAPID_STOMP": 0.92,
    "POLL_COMEBACK_RECOVERY": 0.85,       # raised 0.80→0.85
    # 2026-05-31 — POLL_FIRST_SWING_SETTLE: entry_px filter 0.45-0.85.
    # Cap lowered 0.90→0.85 after analysis: the -$0.90 catastrophic losses were
    # ALL from buying favorites at 0.88-0.90 (tiny upside, full downside). Removing
    # them lifted wr 84%→93%, Sharpe 0.19→0.85, made bootstrap CI provably positive.
    "POLL_FIRST_SWING_SETTLE": 0.85,
    # 2026-05-31 — POLL_REVERSAL_ENTRY: buy underdog, so cap at 0.45
    # (if ask > 0.45 the market already repriced and edge is gone).
    "POLL_REVERSAL_ENTRY": 0.45,
}

# 2026-05-31 — Per-event MIN fill caps from gated-strategy backtest (n=360 events).
# Below these prices, the per-event win-rate craters even when match direction is correct.
#   POLL_VALUE_DISAGREEMENT: 3 trades at px<0.30 went 0-3 (0% wr) vs 98% wr at px>0.70
#   POLL_COMEBACK_RECOVERY:  baseline 70% wr, jumps to 83% at px>0.50
_EVENT_MIN_FILL: dict[str, float] = {
    "POLL_VALUE_DISAGREEMENT":  0.30,
    "POLL_COMEBACK_RECOVERY":   0.50,
    "POLL_FIRST_SWING_SETTLE":  0.45,   # buy-favorite filter from backtest
    # S2: reversal entry — buy the underdog (ask must be BELOW 0.45 to be cheap)
    # MIN_FILL here means global MIN_FILL_PRICE (0.05) applies
    # MAX_FILL set separately below to 0.45
}

# 2026-05-31 — Per-event MAX game time. Some events decay after a critical phase.
#   POLL_STRUCTURAL_DOMINANCE: 100% wr in 15-35 min, drops to 90% at gt>35
_EVENT_MAX_GAME_TIME_SEC: dict[str, int] = {
    "POLL_STRUCTURAL_DOMINANCE": 35 * 60,
    # 2026-06-01 — TUNED: max game time 35→30min for all 5 winner-set events.
    # Full-data sweep (67 matches): the 25-35min segment runs ~72% wr and drags
    # the strategy to 83%; capping at 30min lifts it to 87% AND keeps total $
    # roughly flat ($849→$845) by cutting the noisy late-game entries (teamfight
    # variance, buybacks, comebacks). 30min chosen as the volume/quality middle
    # (25min was best at $927/89% but cut more volume; user picked 30).
    "POLL_FIRST_SWING_SETTLE":     30 * 60,
    "POLL_PHASE_NORMALIZED_LEAD":  30 * 60,
    "POLL_VALUE_DISAGREEMENT":     30 * 60,
    "POLL_RAPID_STOMP":            30 * 60,
    "POLL_DECISIVE_STOMP":         30 * 60,
}

# 2026-05-31 — Strategy simplification after events-vs-S1 analysis.
# Finding: S1 (POLL_FIRST_SWING_SETTLE) returns +$0.236/dollar vs events' +$0.153
# (1.55x more efficient). Events do NOT raise win rate — they're gated to S1's
# direction and amplify its wrong calls. Their only genuine value is COVERAGE
# (matches S1's price filter skips). So: keep S1 + the 3 proven high-volume/edge
# coverage events + terminal high-ground settle events. Demote the rest from
# trading (they still fire for logging/research, just don't trigger entries).
# 2026-06-01 — OPTION 3 "winner set" (user directive, replaces the brief all-31
# experiment). Head-to-head backtest on clean data (band 0.45-0.85, hold-to-settle,
# conf-sized): these 5 events traded 91 trades / 37 matches / 86.8% wr / +$1087,
# vs S1-only's 25 trades / 25 matches / 88% / +$387 — 2.8x the money at the same
# win rate, covering +12 matches S1 skips (which went 20/20 = 100%). The events
# EXCLUDED here (REVERSAL_ENTRY, COMEBACK_RECOVERY, NW_VELOCITY, COMEBACK_FADE,
# LEAD_FLIP, NW_KILL_DIVERGENCE, FIGHT_SWING...) all backtested net-NEGATIVE and
# are blacklisted. Events trade INDEPENDENTLY (first_swing_direction_gate stays
# disabled). See [[experiment-all-events-independent]] and [[strategy-s1-validated]].
WINNER_TRADE_EVENTS: set[str] = {
    "POLL_FIRST_SWING_SETTLE",     # core S1: 37@81%
    "POLL_PHASE_NORMALIZED_LEAD",  # 34@88% — strong, a new find (not in old curated 7)
    "POLL_VALUE_DISAGREEMENT",     # 16@88%
    "POLL_RAPID_STOMP",            # 28@86%
    "POLL_DECISIVE_STOMP",         # 16@88%
}
PRIMARY_TRADE_WHITELIST: set[str] = set(WINNER_TRADE_EVENTS)
BLACKLISTED_EVENTS: set[str] = {e for e in ACTIVE_EVENTS if e not in PRIMARY_TRADE_WHITELIST}

MIN_FILL_PRICE = 0.15
# 2026-05-29 — initially raised 5 → 20 min after event-timing audit, but the
# settlement backtest showed this cost ~$25 over 2 weeks by cutting profitable
# early POLL_FIGHT_SWING trades (16 fires → 7, +$15 → -$12). Reverted to 5 min.
# Per-event gates (FIGHT_SWING_MIN_GAME_TIME_SEC etc) handle event-specific timing.
MIN_GAME_TIME_SEC = 5 * 60
MAX_SIZE_MULTIPLIER = 3.0
_HISTORY_MAXLEN = 300

_KILL_BURST_MIN = 3


def age_ms(ns: int | None) -> int:
    if not ns:
        return 10 ** 9
    return int((time.time_ns() - ns) / 1_000_000)


def time_multiplier(game_time_sec: int | None) -> float:
    if game_time_sec is None:
        return 1.0
    minute = game_time_sec / 60.0
    if minute < 20:
        return 0.65
    if minute < 35:
        return 1.00
    if minute < 45:
        return 1.25
    if minute < 55:
        return 1.45
    return 1.70


def freshness_multiplier(age_sec: float, half_life_sec: float) -> float:
    if half_life_sec <= 0:
        return 1.0
    return 0.5 ** (max(age_sec, 0.0) / half_life_sec)


def _clip_probability(p: float) -> float:
    return min(max(float(p), 0.001), 0.999)


def _logit(p: float) -> float:
    p = _clip_probability(p)
    return log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + exp(-x))


def apply_probability_move(anchor_price: float, impact_cents: float) -> float:
    """Apply a probability shock in logit space.

    The event table is calibrated in approximate probability/cents around 50%.
    Applying it in logit space avoids impossible prices near 0/1 and produces
    more realistic edge checks than anchor + impact.
    """
    return _clip_probability(_sigmoid(_logit(anchor_price) + impact_cents * 4.0))


def _event_attr(event: Any, key: str, default: Any = None) -> Any:
    if isinstance(event, dict):
        return event.get(key, default)
    return getattr(event, key, default)


def _cadence_signal_metadata(event: Any) -> dict[str, Any]:
    fields = (
        "event_schema_version",
        "snapshot_gap_sec",
        "actual_window_sec",
        "networth_delta",
        "kill_diff_delta",
        "total_kills_delta",
        "networth_delta_per_30s",
        "kill_diff_delta_per_30s",
        "source_cadence_quality",
        # 2026-05-26 — pass through event_confidence and severity for premium-tier
        # sizing checks in live_executor (see PREMIUM_EVENT_FILTERS).
        "event_confidence",
        "severity",
    )
    return {field: _event_attr(event, field) for field in fields}


def _event_quality_score(events: Iterable[Any]) -> float:
    scores: list[float] = []
    for event in events:
        explicit = _event_attr(event, "event_quality")
        if explicit is not None:
            try:
                scores.append(float(explicit))
                continue
            except (TypeError, ValueError):
                pass
        base = float(_event_attr(event, "base_pressure_score", 0.0) or 0.0)
        conversion = float(_event_attr(event, "conversion_score", 0.0) or 0.0)
        fight = float(_event_attr(event, "fight_pressure_score", 0.0) or 0.0)
        economy = float(_event_attr(event, "economic_pressure_score", 0.0) or 0.0)
        scores.append((0.35 * base) + (0.25 * conversion) + (0.20 * fight) + (0.20 * economy))
    return round(max(scores, default=0.0), 4)


def _execution_quality_scores(book_age: int, spread: float | None, ask: float, ask_size: Any) -> dict[str, float]:
    book_freshness_score = max(0.0, min(1.0, 1.0 - (book_age / max(MAX_BOOK_AGE_MS, 1))))
    spread_score = 0.6 if spread is None else max(0.0, min(1.0, 1.0 - (spread / max(MAX_SPREAD, 0.0001))))
    notional = None
    try:
        if ask_size is not None:
            notional = ask * float(ask_size)
    except (TypeError, ValueError):
        notional = None
    size_score = 0.6 if notional is None else max(0.0, min(1.0, notional / max(MIN_ASK_SIZE_USD, 0.01)))
    price_not_chased_score = 0.0 if ask >= 0.97 else (0.25 if ask >= 0.95 else (0.65 if ask >= 0.90 else 1.0))
    execution_quality = book_freshness_score * spread_score * size_score * price_not_chased_score
    price_quality = spread_score * price_not_chased_score
    return {
        "book_freshness_score": round(book_freshness_score, 4),
        "spread_score": round(spread_score, 4),
        "size_score": round(size_score, 4),
        "price_not_chased_score": round(price_not_chased_score, 4),
        "price_quality_score": round(price_quality, 4),
        "execution_quality_score": round(execution_quality, 4),
    }


def apply_suppressions(events: Iterable[Any]) -> list[Any]:
    out = list(events)
    event_types = {_event_attr(e, "event_type") for e in out}
    suppressed: set[str] = set()
    for winner, losers in SUPPRESSIONS.items():
        if winner in event_types:
            if winner in PRIMARY_TRADE_EVENTS:
                suppressed.update(losers)
            else:
                # Research/non-primary events may not suppress primary tradeable events.
                suppressed.update(losers - PRIMARY_TRADE_EVENTS)
    return [e for e in out if _event_attr(e, "event_type") not in suppressed]


try:
    import winprob as _winprob
except Exception:
    _winprob = None


def _s3_fair(lead: int, game_time_sec, elo_diff=None) -> float:
    """Calibrated win-probability for the backed side. Primary source is the
    `winprob` model — a symmetric logistic fit on 1000 OpenDota pro matches
    (P(win | net-worth lead, game-minute, team-Elo gap), CV log-loss 0.46).
    elo_diff (backed − opp Elo) is clamped/shrunk inside winprob; pass None when
    either team's Elo is unknown (gold-only fair). Falls back to the legacy 2D
    empirical table if the model module is unavailable."""
    if _winprob is not None and os.getenv("S3_USE_WINPROB", "true").lower() == "true":
        try:
            return _winprob.fair(lead, game_time_sec, elo_diff)
        except Exception:
            pass
    a = abs(int(lead))
    g = int(game_time_sec or 0)
    tb = 0 if g < 900 else (1 if g < 1800 else 2)   # <15m / 15-30m / 30m+
    if a < 2000:
        return 0.55                                  # coin flip (filtered by min-lead)
    if a < 5000:
        return [0.80, 0.74, 0.58][tb]                # empirical 82/76/58
    if a < 10000:
        return [0.95, 0.86, 0.69][tb]                # empirical 99/88/69
    return [0.95, 0.94, 0.90][tb]                    # empirical 100/96/92 (capped)


def _s3_fair_from_lead(lead: int) -> float:
    """Back-compat shim — phase-unaware fallback when game_time is unavailable."""
    return _s3_fair(lead, None)


_TEAM_ELO_CACHE: dict[str, float] = {}
_TEAM_ELO_MTIME: float = 0.0


def _load_team_elo() -> dict[str, float]:
    """team_id -> OpenDota Elo, reloaded when logs/opendota_teams.json changes."""
    global _TEAM_ELO_CACHE, _TEAM_ELO_MTIME
    import os as _os, json as _json
    path = "logs/opendota_teams.json"
    try:
        mt = _os.path.getmtime(path)
        if mt != _TEAM_ELO_MTIME:
            d = _json.load(open(path))
            _TEAM_ELO_CACHE = {str(k): float(v["rating"]) for k, v in d.items()
                               if isinstance(v, dict) and v.get("rating")}
            _TEAM_ELO_MTIME = mt
    except Exception:
        pass
    return _TEAM_ELO_CACHE


def _s3_team_elos(event_direction: str, game: dict):
    """(backed_elo, opp_elo) from the game's team_ids; (None,None) if unknown."""
    cache = _load_team_elo()
    er = cache.get(str(game.get("radiant_team_id") or ""))
    ed = cache.get(str(game.get("dire_team_id") or ""))
    if event_direction == "radiant":
        return er, ed
    if event_direction == "dire":
        return ed, er
    return None, None


def _event_team_lead(event_direction: str, game: dict) -> int | None:
    lead = game.get("radiant_lead")
    try:
        lead = int(lead)
    except (TypeError, ValueError):
        return None
    if event_direction == "radiant":
        return lead
    if event_direction == "dire":
        return -lead
    return None


def _side_bits_for_enemy(event_direction: str, game: dict) -> int | None:
    """Return currently alive enemy structures for the event-favored side.

    GetTopLiveGame building_state is not the standard 11-bit tower_state mask.
    Do not derive structure context from it until its layout is decoded. The
    tower_state fallback only contains Radiant alive buildings, so it can only
    describe Dire-favoring attacks against Radiant.
    """
    bs = game.get("building_state")
    if bs is not None:
        try:
            bs = int(bs)
        except (TypeError, ValueError):
            bs = None
    if bs is not None:
        return None

    ts = game.get("tower_state")
    if ts is not None and event_direction == "dire":
        try:
            return int(ts) & 0x7FF
        except (TypeError, ValueError):
            return None
    return None


def _structure_context(event_direction: str, game: dict) -> dict[str, int] | None:
    bits = _side_bits_for_enemy(event_direction, game)
    if bits is None:
        return None
    t2_mask = (1 << 1) | (1 << 4) | (1 << 7)
    t3_mask = (1 << 2) | (1 << 5) | (1 << 8)
    t4_mask = (1 << 9) | (1 << 10)
    t2_alive = (bits & t2_mask).bit_count()
    t3_alive = (bits & t3_mask).bit_count()
    t4_alive = (bits & t4_mask).bit_count()
    return {
        "enemy_t2_alive": t2_alive,
        "enemy_t2_dead": 3 - t2_alive,
        "enemy_t3_alive": t3_alive,
        "enemy_t3_dead": 3 - t3_alive,
        "enemy_t4_alive": t4_alive,
        "enemy_t4_dead": 2 - t4_alive,
    }


class EventSignalEngine:
    """Event-driven latency-arb signal engine.

    The live path should call evaluate_cluster(): one primary event plus any
    same-direction confirmations becomes a single capped probability shock.
    evaluate() is kept as a backwards-compatible one-event wrapper.
    """

    def __init__(self):
        self._price_history: dict[str, deque] = {}
        self._pregame_price: dict[str, float] = {}
        self._last_signal_ms: dict[tuple[str, str, str], int] = {}

    def record_price(self, token_id: str, mid: float, game_time_sec: int | None = None):
        hist = self._price_history.setdefault(token_id, deque(maxlen=_HISTORY_MAXLEN))
        hist.append((int(time.time() * 1000), mid))

        if game_time_sec is None or game_time_sec <= 0:
            self._pregame_price[token_id] = mid
        elif token_id not in self._pregame_price:
            self._pregame_price[token_id] = mid

    def _price_n_seconds_ago(self, token_id: str, n_sec: float) -> float | None:
        hist = self._price_history.get(token_id)
        if not hist:
            return None
        cutoff_ms = int(time.time() * 1000) - int(n_sec * 1000)
        for wall_ms, price in reversed(hist):
            if wall_ms <= cutoff_ms:
                return price
        return None

    def _current_price(self, token_id: str) -> float | None:
        hist = self._price_history.get(token_id)
        return hist[-1][1] if hist else None

    def evaluate(
        self,
        event_type: str,
        event_direction: str,
        event_delta: float | None,
        game: dict,
        mapping: dict,
        yes_book: dict | None,
        no_book: dict | None,
        severity: str = "",
    ) -> dict:
        event = {
            "event_type": event_type,
            "direction": event_direction,
            "delta": event_delta,
            "severity": severity,
            "game_time_sec": game.get("game_time_sec"),
        }
        return self.evaluate_cluster(
            [event], game, mapping, yes_book, no_book,
            require_primary=False,
        )

    def evaluate_cluster(
        self,
        events: Iterable[Any],
        game: dict,
        mapping: dict,
        yes_book: dict | None,
        no_book: dict | None,
        require_primary: bool = True,
        fair_price_override: float | None = None,
        fair_source: str | None = None,
    ) -> dict:
        events = [e for e in apply_suppressions(events) if _event_attr(e, "event_type") in ACTIVE_EVENTS]
        # 2026-05-31 — Blacklist filter (low-conviction events identified in gated-strategy backtest)
        events = [e for e in events if _event_attr(e, "event_type") not in BLACKLISTED_EVENTS]
        if not events:
            return {"decision": "skip", "reason": "event_type_inactive"}

        # 2026-05-30 — Tier-1 league allowlist. If TIER1_LEAGUE_IDS is set
        # (comma-separated env), skip any game whose league_id isn't in it.
        # Empty / unset = allow all leagues (legacy behaviour).
        import os
        _tier1 = os.getenv("TIER1_LEAGUE_IDS", "").strip()
        if _tier1:
            _allowed = {x.strip() for x in _tier1.split(",") if x.strip()}
            _game_lid = str(game.get("league_id") or "")
            if _game_lid not in _allowed:
                return {
                    "decision": "skip",
                    "reason": f"league_not_allowed:{_game_lid or 'none'}",
                }

        # Choose the strongest active event's direction and discard contrary events.
        events.sort(key=lambda e: ACTIVE_EVENTS[_event_attr(e, "event_type")].base, reverse=True)
        event_direction = _event_attr(events[0], "direction") or ""
        events = [e for e in events if (_event_attr(e, "direction") or "") == event_direction]
        if not events or not event_direction:
            return {"decision": "skip", "reason": "event_direction_unknown"}

        if require_primary and not any(_event_attr(e, "event_type") in PRIMARY_TRADE_EVENTS for e in events):
            primary_event_type = _event_attr(events[0], "event_type")
            return {
                "decision": "skip",
                "reason": "no_primary_event",
                "event_type": primary_event_type,
                "event_tier": event_tier(primary_event_type),
                "event_is_primary": event_is_primary(primary_event_type),
            }

        market_type = str(mapping.get("market_type") or "").upper()
        is_map3_proxy = (
            market_type == "MATCH_WINNER"
            and ENABLE_MATCH_WINNER_GAME3_PROXY
            and is_game3_match_proxy(mapping)
        )

        if market_type not in ("MAP_WINNER", "MATCH_WINNER"):
            return {"decision": "skip", "reason": "unsupported_market_type"}

        if market_type == "MATCH_WINNER" and not (is_map3_proxy or ENABLE_MATCH_WINNER_TRADING):
            return {
                "decision": "skip",
                "reason": "match_winner_disabled",
                **market_scope_metadata(mapping),
            }

        game_time = game.get("game_time_sec")
        if game_time is not None and game_time < MIN_GAME_TIME_SEC:
            return {"decision": "skip", "reason": "game_too_early", "game_time_sec": game_time}

        event_types_present = {_event_attr(e, "event_type") for e in events}
        # 2026-05-28 — Phase A.2 gates from deep_data_study (n=308 trades, 76 matches):
        #   <15min       n=24 +$0.04/t 46% win — too early, signal not yet meaningful
        #   45-50min     n=26 +$0.13/t 38% win — late-game weakness window
        #   >60min       n=11 −$0.05/t 55% win — overtime, edge has decayed
        # These three gates apply to ALL primary events (not event-specific).
        if game_time is not None:
            # 2026-05-31 — lowered 900→600 (15min→10min). FIRST_SWING_SETTLE and
            # REVERSAL_ENTRY have their own per-event gt>600 gate. The old 15min
            # global was blocking valid entries on fast games where the market
            # moves to 0.97+ before 15min.
            _primary_et = _event_attr(events[0], "event_type") if events else ""
            _phase_min = 600 if _primary_et in (
                "POLL_FIRST_SWING_SETTLE", "POLL_REVERSAL_ENTRY",
                "POLL_PHASE_NORMALIZED_LEAD",
            ) else 900
            if game_time < _phase_min:
                return {"decision": "skip", "reason": "phase_too_early", "game_time_sec": game_time}
            if 2700 <= game_time < 3000:
                return {"decision": "skip", "reason": "phase_45_50m_weak", "game_time_sec": game_time}
            if game_time >= 3600:
                return {"decision": "skip", "reason": "phase_late_game", "game_time_sec": game_time}
            # 2026-05-31 — Per-event max-game-time gate (events decay after a critical phase)
            for _ev in events:
                _et = _event_attr(_ev, "event_type")
                _max_gt = _EVENT_MAX_GAME_TIME_SEC.get(_et)
                if _max_gt is not None and game_time > _max_gt:
                    return {
                        "decision": "skip",
                        "reason": "event_past_max_game_time",
                        "event_type": _et,
                        "game_time_sec": game_time,
                        "max_game_time_sec": _max_gt,
                    }
        if (
            FIGHT_SWING_MAX_GAME_TIME_SEC > 0
            and "POLL_FIGHT_SWING" in event_types_present
            and game_time is not None
            and game_time > FIGHT_SWING_MAX_GAME_TIME_SEC
        ):
            return {"decision": "skip", "reason": "fight_swing_too_late", "game_time_sec": game_time}
        if (
            FIGHT_SWING_MIN_GAME_TIME_SEC > 0
            and "POLL_FIGHT_SWING" in event_types_present
            and game_time is not None
            and game_time < FIGHT_SWING_MIN_GAME_TIME_SEC
        ):
            return {"decision": "skip", "reason": "fight_swing_too_early", "game_time_sec": game_time}

        steam_age = age_ms(game.get("received_at_ns"))
        # 2026-05-31 — Bypass in paper mode (same rationale as source_update_stale
        # below). Backtest audit: steam_stale alone blocked 102/377 (27%) of would-be
        # winning trades. For paper we want to see what the strategy WOULD do.
        from config import ENABLE_REAL_LIVE_TRADING as _ERLT_SS
        if _ERLT_SS and steam_age > MAX_STEAM_AGE_MS:
            return {"decision": "skip", "reason": "steam_stale", "steam_age_ms": steam_age}

        data_source = game.get("data_source")
        if REQUIRE_TOP_LIVE_FOR_SIGNALS and data_source != "top_live":
            return {
                "decision": "skip", "reason": "non_top_live_source",
                "data_source": data_source, "steam_age_ms": steam_age,
            }

        source_update_age_sec = game.get("source_update_age_sec")
        if source_update_age_sec is not None:
            try:
                source_update_age_sec = float(source_update_age_sec)
            except (TypeError, ValueError):
                source_update_age_sec = None
        # 2026-05-30 — Bypass in paper mode. The source_update gate exists to
        # protect real capital from stale Polymarket data; in paper we want to
        # see what the strategy WOULD do, so we trade through.
        from config import ENABLE_REAL_LIVE_TRADING as _ERLT_SU
        if _ERLT_SU and source_update_age_sec is not None and source_update_age_sec > MAX_SOURCE_UPDATE_AGE_SEC:
            return {
                "decision": "skip", "reason": "source_update_stale",
                "source_update_age_sec": round(source_update_age_sec, 3),
                "steam_age_ms": steam_age,
            }

        stream_delay_s = game.get("stream_delay_s")
        if stream_delay_s is not None:
            try:
                stream_delay_s = float(stream_delay_s)
            except (TypeError, ValueError):
                stream_delay_s = None
        # stream_delay_s is Valve's spectator/broadcast delay metadata, not proof
        # that GetTopLiveGame itself is stale. Keep it for logs/research only;
        # freshness guards must use received_at_ns, plausible last_update_time,
        # book age, and market repricing.

        yes_team = norm_team(mapping.get("yes_team"))
        radiant_team = norm_team(game.get("radiant_team"))
        dire_team = norm_team(game.get("dire_team"))

        # Primary side detection: string match against radiant/dire names
        if yes_team and radiant_team and yes_team == radiant_team:
            event_favors_yes = (event_direction == "radiant")
        elif yes_team and dire_team and yes_team == dire_team:
            event_favors_yes = (event_direction == "dire")
        else:
            # Fallback: use the robust direction saved during sync_markets
            side_map = mapping.get("steam_side_mapping")  # "normal" or "reversed"
            if side_map == "normal":
                event_favors_yes = (event_direction == "radiant")
            elif side_map == "reversed":
                event_favors_yes = (event_direction == "dire")
            else:
                return {"decision": "skip", "reason": "team_side_unknown"}

        if event_favors_yes:
            token_book = yes_book
            token_id = mapping.get("yes_token_id", "")
            side = "YES"
        else:
            token_book = no_book
            token_id = mapping.get("no_token_id", "")
            side = "NO"

        # 2026-06-01 — ALL-EVENTS-INDEPENDENT mode: the first-swing direction lock
        # is DISABLED. Each event trades on its own direction, independent of S1.
        # (Was: block any event whose direction disagreed with the locked first
        # swing. That coupling is exactly what "make them all independent" removes.)
        primary_event_type = _event_attr(events[0], "event_type")
        event_quality = _event_quality_score(events)
        base_metadata = {
            "event_type": primary_event_type,
            "event_tier": event_tier(primary_event_type),
            "event_is_primary": event_is_primary(primary_event_type),
            "event_family": event_family(primary_event_type),
            "event_quality": event_quality,
            "event_direction": event_direction,
            "token_id": token_id,
            "side": side,
            **_cadence_signal_metadata(events[0]),
        }

        if not token_book or token_book.get("best_ask") is None:
            return {"decision": "skip", "reason": "missing_book", **base_metadata}

        book_age = age_ms(token_book.get("received_at_ns"))
        # 2026-05-30 — Bypass book_stale in paper mode (same rationale as
        # source_update_stale): we want to evaluate the strategy on whatever
        # price the bot last saw, not skip silently.
        from config import ENABLE_REAL_LIVE_TRADING as _ERLT_BS
        if _ERLT_BS and book_age > MAX_BOOK_AGE_MS:
            return {"decision": "skip", "reason": "book_stale", "book_age_ms": book_age, **base_metadata}

        ask = float(token_book["best_ask"])
        bid = token_book.get("best_bid")
        mid = (ask + float(bid)) / 2.0 if bid is not None else ask
        spread = (ask - float(bid)) if bid is not None else None
        ask_size = token_book.get("ask_size")
        execution_scores = _execution_quality_scores(book_age, spread, ask, ask_size)

        # Underdog reversal: when a comeback event fires at cheap price, relax entry filters.
        # The asymmetric payoff (entry 0.10→settle 1.00) changes the edge math entirely.
        _is_underdog_reversal = (
            primary_event_type in UNDERDOG_REVERSAL_EVENTS
            and UNDERDOG_REVERSAL_MIN_ENTRY <= ask <= UNDERDOG_REVERSAL_MAX_ENTRY
        )

        if ask < MIN_FILL_PRICE and not _is_underdog_reversal:
            return {"decision": "skip", "reason": "fill_price_too_low", **base_metadata, "ask": ask, "mid": mid}

        # 2026-05-31 — Per-event MIN fill gate (stricter than global MIN_FILL_PRICE for select events)
        _per_event_min = _EVENT_MIN_FILL.get(primary_event_type)
        if _per_event_min is not None and ask < _per_event_min and not _is_underdog_reversal:
            return {
                "decision": "skip",
                "reason": "event_fill_price_too_low",
                **base_metadata,
                "ask": ask, "mid": mid,
                "event_min_fill": _per_event_min,
            }

        if ask >= 0.97 and primary_event_type != "THRONE_EXPOSED" and primary_event_type not in {"POLL_FIGHT_SWING", "POLL_LATE_FIGHT_FLIP", "POLL_TEAM_WIPE", "POLL_KILL_BURST_CONFIRMED"}:
            return {
                "decision": "skip", "reason": "chasing_terminal_price",
                **base_metadata,
                "ask": ask, "mid": mid, "event_type": primary_event_type,
                "event_tier": event_tier(primary_event_type), "event_family": event_family(primary_event_type),
                "event_quality": event_quality, **execution_scores,
            }
        if (
            ask >= 0.95
            and primary_event_type in {
                "OBJECTIVE_CONVERSION_T3", "OBJECTIVE_CONVERSION_T4",
                "BASE_PRESSURE_T3_COLLAPSE", "BASE_PRESSURE_T4", "THRONE_EXPOSED",
            }
        ):
            return {
                "decision": "skip", "reason": "priced_out_high_ground_stomp",
                **base_metadata,
                "ask": ask, "mid": mid, "event_type": primary_event_type,
                "event_tier": event_tier(primary_event_type), "event_family": event_family(primary_event_type),
                "event_quality": event_quality, **execution_scores,
            }

        # 4. Fill Price Guard: Don't chase extreme prices relative to historical alpha
        # or buy into "throw-prone" late game leads.
        # NEW: Asymmetric Fill Caps
        #   Underdog Reversal: High Cap (0.85) - let the drift happen.
        #   Favorite Confirmation: Low Cap (0.60) - don't buy the top.
        # NOTE: current_price (price-history snapshot) isn't computed until below,
        # so use ask here. The post-snapshot underdog check at line ~633 uses
        # current_price intentionally for downstream POLL_VALUE_DISAGREEMENT logic.
        is_underdog_reversal_by_ask = (ask < 0.50)

        max_fill = _EVENT_MAX_FILL.get(primary_event_type, DEFAULT_MAX_FILL_PRICE)
        effective_max_fill = max_fill
        if primary_event_type in {"POLL_FIGHT_SWING", "POLL_TEAM_WIPE", "POLL_KILL_BURST_CONFIRMED", "POLL_AEGIS_MOMENTUM"}:
            # Favorite-confirmation cap raised 0.90 → 0.95 from user request:
            # Audit showed combat alpha persists even at high prices.
            effective_max_fill = 0.95 if is_underdog_reversal_by_ask else 0.95

        # POLL_VALUE_DISAGREEMENT not_an_underdog gate REMOVED 2026-05-25:
        # B4 relaxed-cap backtest showed the alpha lives in 0.45-0.75 (n=53,
        # mean@settle=+1.2, win=88%), not just <0.50. Cap at 0.75 (above)
        # now does the work. Semantic: event is no longer "underdog comeback
        # only", it's "any settlement-priced value disagreement up to 0.75".

        if ask > effective_max_fill:
            return {
                "decision": "skip", "reason": "fill_price_too_high",
                **base_metadata,
                "ask": ask, "mid": mid, "cap": effective_max_fill,
                "event_type": primary_event_type, "event_tier": event_tier(primary_event_type),
                "event_family": event_family(primary_event_type), "event_quality": event_quality,
                **execution_scores,
            }

        if primary_event_type in {
            "POLL_COMEBACK_RECOVERY",
            "POLL_MAJOR_COMEBACK_RECOVERY",
            "POLL_LEAD_FLIP_WITH_KILLS",
            "POLL_STOMP_THROW_CONFIRMED",
        } and spread is not None and spread > 0.12 and not _is_underdog_reversal:
            return {
                "decision": "skip", "reason": "wide_spread_comeback_alert",
                **base_metadata,
                "spread": spread, "ask": ask, "mid": mid, "event_type": primary_event_type,
                "event_tier": event_tier(primary_event_type), "event_family": event_family(primary_event_type),
                "event_quality": event_quality, **execution_scores,
            }

        # 2026-05-31 — Tiered nw/kill filter REMOVED after volume/quality frontier
        # analysis. It cut 6 trades (22→16) chasing 100% in-sample win rate, but
        # those 6 were net POSITIVE in aggregate: 22 trades @ 91% = +$393 vs 16 @
        # 100% = +$318. Total return beats win-rate vanity. Cheap-agree entries
        # stay (the mispricing-tier 1.5x sizing in the size_multiplier still
        # rewards the disagree case; we just no longer hard-block the agree case).

        if spread is not None and spread > MAX_VOLATILITY_SPREAD:
            return {
                "decision": "skip", "reason": "volatility_spread_too_wide", "spread": spread,
                **base_metadata,
                "event_type": primary_event_type, "event_tier": event_tier(primary_event_type),
                "event_family": event_family(primary_event_type), "event_quality": event_quality,
                **execution_scores,
            }

        _spread_cap = MAX_SPREAD * 2.0 if market_type == "MATCH_WINNER" else MAX_SPREAD
        if spread is not None and spread > _spread_cap:
            return {
                "decision": "skip", "reason": "spread_too_wide", "spread": spread,
                **base_metadata,
                "event_type": primary_event_type, "event_tier": event_tier(primary_event_type),
                "event_family": event_family(primary_event_type), "event_quality": event_quality,
                **execution_scores,
            }

        if ask_size is not None and ask * float(ask_size) < MIN_ASK_SIZE_USD:
            return {
                "decision": "skip", "reason": "insufficient_ask_size",
                **base_metadata,
                "event_type": primary_event_type, "event_tier": event_tier(primary_event_type),
                "event_family": event_family(primary_event_type), "event_quality": event_quality,
                **execution_scores,
            }

        match_id = str(game.get("match_id") or game.get("lobby_id") or "")
        cooldown_key = (match_id, event_direction, primary_event_type)
        now_ms = int(time.time() * 1000)
        cooldown_ms = max(60_000, int(PRICE_LOOKBACK_SEC * 1000))
        if now_ms - self._last_signal_ms.get(cooldown_key, 0) < cooldown_ms:
            return {"decision": "skip", "reason": "cooldown", **base_metadata}

        current_price = self._current_price(token_id)
        if current_price is None:
            # 2026-05-31 — Hold-to-settle events don't use price history for momentum
            # (they hold to $0/$1, not a 30s markout). When a match is bound MID-GAME
            # the engine has no accumulated price history for the fresh token yet —
            # which was silently dropping S1 entries on newly-bound matches (the exact
            # scenario hit on LGD/NaVi today). Fall back to the live book ask so the
            # hold-to-settle entry can still fire.
            from config import EXIT_HORIZON_BY_EVENT as _EH_NPH
            _is_hts_nph = _EH_NPH.get(primary_event_type, None) == 0
            if _is_hts_nph and ask is not None and 0 < float(ask) < 1:
                current_price = float(ask)
            else:
                return {"decision": "skip", "reason": "no_price_history", **base_metadata}

        # POLL_VALUE_DISAGREEMENT secondary "underdog" gate REMOVED 2026-05-25:
        # the earlier ask-based gate was removed (see ~line 576) based on B4
        # relaxed-cap backtest. The cap at 0.75 (`_EVENT_MAX_FILL`) now defines
        # acceptable entry. `is_underdog_reversal` is still computed for
        # downstream signed-edge / lag tuning, but no longer gates entry.
        is_underdog_reversal = (current_price < 0.50)

        # 1. Repricing check: if the market already moved significantly in the
        # last 5s, the edge is likely gone.
        anchor_5s = self._price_n_seconds_ago(token_id, 5)
        if anchor_5s is not None:
            recent_repriced_move = current_price - anchor_5s
            # 2026-05-27: Relaxed to 5c for combat signals (from 1.5x MIN_LAG).
            # These structural drifts survive the initial reprice pop.
            repriced_gate = 0.05 if primary_event_type in {"POLL_FIGHT_SWING", "POLL_LATE_FIGHT_FLIP", "POLL_TEAM_WIPE", "POLL_KILL_BURST_CONFIRMED"} else (MIN_LAG * 1.5)
            if recent_repriced_move > repriced_gate:
                return {
                    "decision": "skip", "reason": "already_repriced",
                    **base_metadata,
                    "move_5s": round(recent_repriced_move, 4),
                    "current_price": round(current_price, 4),
                    "anchor_5s": round(anchor_5s, 4),
                }

        anchor_price = self._price_n_seconds_ago(token_id, PRICE_LOOKBACK_SEC)
        if anchor_price is None:
            anchor_price = self._pregame_price.get(token_id)
            if anchor_price is None:
                # 2026-05-31 — Hold-to-settle events don't use anchor_price for
                # momentum (market_move/expected_move are irrelevant when holding
                # to $0/$1). On mid-game-bound matches there's no price history to
                # anchor against. Fall back to current_price (→ market_move=0) so
                # the entry still fires. Same fix as no_price_history above.
                from config import EXIT_HORIZON_BY_EVENT as _EH_AP
                if _EH_AP.get(primary_event_type, None) == 0:
                    anchor_price = current_price
                else:
                    return {"decision": "skip", "reason": "insufficient_price_history", **base_metadata}

        # Freshness check for internal Valve stats — skip only if stale AND not top_live.
        # top_live already provides aggregate NW lead directly; realtime stats are optional enrichment.
        rt_age = game.get("realtime_stats_age_sec")
        if rt_age is not None and rt_age > 20.0 and game.get("data_source") != "top_live":
            return {"decision": "skip", "reason": "valve_stats_stale", "rt_age_sec": rt_age}

        adjusted_values = [self._adjusted_event_value(e, game) for e in events]
        expected_move = self._combine_event_impacts(adjusted_values)
        expected_move = min(expected_move, self._state_cap(events, game))
        expected_move *= self._context_multiplier(events, game)

        # Spread-width boost for FIGHT_SWING: real signal starts at 4c (1.30x lift), 5c+ (1.51x).
        # Data: mid/late, spread≥4c → avg|fwd30|=0.058 vs baseline 0.045. Threshold raised from 2.5c.
        if "POLL_FIGHT_SWING" in event_types_present and spread is not None and spread >= 0.04:
            spread_boost = min(1.0 + (spread - 0.04) * 8.0, 1.5)
            expected_move *= spread_boost

        market_move = current_price - anchor_price
        fair_price = apply_probability_move(anchor_price, expected_move)
        if fair_price_override is not None:
            try:
                fair_price = _clip_probability(float(fair_price_override))
                expected_move = fair_price - anchor_price
            except (TypeError, ValueError):
                fair_price_override = None
        executable_price = min(ask + PAPER_SLIPPAGE_CENTS, 0.99)
        remaining_move = fair_price - current_price

        if market_type == "MATCH_WINNER" and fair_price_override is None:
            if is_map3_proxy:
                fair_source = "match_winner_game3_proxy"
            else:
                # Scale expected_move by BO3 series sensitivity.
                # Series win probability moves at a fraction of the current-map rate:
                #   G1 (0-0): sensitivity = 2 * p_next * (1-p_next)  ≈ 0.5
                #   G2 (1-0): sensitivity = 1 - p_next               ≈ 0.5
                #   G2 (0-1): sensitivity = p_next                   ≈ 0.5
                try:
                    p_next = float(mapping.get("p_next_yes") or 0.5)
                    p_next = max(0.01, min(0.99, p_next))
                    score_yes = int(mapping.get("series_score_yes") or 0)
                    score_no = int(mapping.get("series_score_no") or 0)
                    gnum = int(
                        mapping.get("current_game_number")
                        or mapping.get("game_number")
                        or 1
                    )
                    if gnum == 1:
                        sensitivity = 2 * p_next * (1 - p_next)
                    elif gnum == 2:
                        sensitivity = (1 - p_next) if score_yes >= score_no else p_next
                    else:
                        sensitivity = 1.0
                except (TypeError, ValueError):
                    sensitivity = 0.5
                series_move = expected_move * sensitivity
                fair_price = apply_probability_move(anchor_price, series_move)
                expected_move = series_move
                fair_source = "match_winner_bo3_sensitivity"

        # 2026-05-27 — REMOVED the +0.096 "calibration offset" which was
        # the source of the +9c claimed edge vs -1.5c realized gap.
        # The audit showed this offset was overconfident bias, not bug fix.
        # The re-calibrated EventSpec.base values now produce realistic moves.
        # fair_price = _clip_probability(fair_price + 0.096)  # disabled

        remaining_move = fair_price - current_price

        # 2026-05-27 — Adverse-selection discount.
        # Audit found the bot detects events ~3-5s AFTER the move started; by the
        # time it buys, the market has already moved ~half the expected_move.
        # Subtract half the spread + half of expected_move to approximate the
        # adverse selection cost.
        _book = yes_book if side == "YES" else no_book
        _ba = _book.get("best_ask") if _book else None
        _bb = _book.get("best_bid") if _book else None
        try:
            _spread = max(0.0, float(_ba) - float(_bb)) if (_ba and _bb) else 0.0
        except (TypeError, ValueError):
            _spread = 0.0
        adv_sel_discount = _spread / 2.0 + max(0.0, expected_move) * 0.5
        executable_edge = fair_price - executable_price - adv_sel_discount
        lag = remaining_move

        momentum_chase_cap = MAX_MOMENTUM_CHASE
        if "POLL_RAPID_STOMP" in event_types_present or "POLL_DECISIVE_STOMP" in event_types_present:
            momentum_chase_cap = 0.30
        elif "POLL_FIGHT_SWING" in event_types_present:
            # Disable momentum cap for combat turns (Structural drift)
            momentum_chase_cap = 2.0 

        if expected_move > 0 and market_move / expected_move > momentum_chase_cap:
            return {
                "decision": "skip", "reason": "momentum_exhausted",
                **base_metadata, "market_move": round(market_move, 4),
                "expected_move": round(expected_move, 4),
                "momentum_chase_cap": momentum_chase_cap,
            }

        required_edge = self._required_edge(events, game, ask, spread)
        
        # Volatility check: if the market just moved sharply in our direction (3s),
        # require extra edge to avoid buying the "peak" of a spike.
        if market_move > 0.10:
            required_edge += 0.05
        elif market_move > 0.06:
            required_edge += 0.02

        # Increase uncertainty_penalty for low-confidence structure events
        structure_uncertainty_penalty = 0.0
        for e in events:
            etype = _event_attr(e, "event_type")
            if etype in {
                "OBJECTIVE_CONVERSION_T2", "OBJECTIVE_CONVERSION_T3", "OBJECTIVE_CONVERSION_T4",
                "THRONE_EXPOSED", "BASE_PRESSURE_T4", "BASE_PRESSURE_T3_COLLAPSE",
            }:
                struct_conf = _event_attr(e, "structure_confidence")
                if struct_conf is not None and float(struct_conf) < 1.0:
                    structure_uncertainty_penalty += (1.0 - float(struct_conf)) * 0.06
        
        required_edge += structure_uncertainty_penalty
        
        # New: Sniper Reversal Rule
        # Underdogs are higher value; reduce thresholds to capture the flip.
        effective_required_edge = UNDERDOG_REVERSAL_MIN_EDGE if is_underdog_reversal else required_edge

        # 2026-05-30 — Bypass executable_edge gate for hold-to-settle events.
        # The expected_move / executable_edge calculation is short-horizon-
        # markout logic: it requires the price to still have room to move past
        # our entry before we exit on a 30-60s window. For events with
        # EXIT_HORIZON=0 we hold until game_over and collect $0/$1; the only
        # relevant EV gate is per-event _EVENT_MAX_FILL (set from historical
        # settle win rate). Adverse-selection / late-detection adds slip but
        # doesn't invert the EV: an event with 93% settle win rate is +EV at
        # any ask < 0.93 regardless of pre-entry market move.
        from config import EXIT_HORIZON_BY_EVENT as _EXIT_H
        _is_hold_to_settle = (_EXIT_H.get(primary_event_type, None) == 0)

        # --- S3 net-worth value gate (hold-to-settle events) ---
        # The edge is net-worth-predicts-winner, not the detector. Require the
        # backed side to genuinely lead AND the calibrated win-prob to exceed the
        # price by a margin. Replaces reliance on per-event _EVENT_MAX_FILL caps.
        if _is_hold_to_settle and S3_ENABLED:
            _lead = _event_team_lead(event_direction, game)
            if _lead is None or _lead < S3_MIN_NW_LEAD:
                return {
                    "decision": "skip", "reason": "s3_nw_lead_too_small",
                    **base_metadata, "radiant_lead": _lead,
                    "executable_price": round(executable_price, 4),
                }
            _be, _oe = _s3_team_elos(event_direction, game) if S3_ELO_ENABLED else (None, None)
            if _be is not None and _oe is not None and _be < _oe - S3_ELO_MARGIN:
                return {
                    "decision": "skip", "reason": "s3_team_too_weak",
                    **base_metadata, "backed_elo": _be, "opp_elo": _oe,
                    "executable_price": round(executable_price, 4),
                }
            # Elo gap (backed − opponent) feeds the win-prob model; None if unknown.
            _elo_diff = None
            if _winprob is not None:
                _rt, _dt = game.get("radiant_team_id"), game.get("dire_team_id")
                _rn, _dn = game.get("radiant_team"), game.get("dire_team")
                if event_direction == "radiant":
                    _elo_diff = _winprob.elo_diff(_rt, _dt, _rn, _dn)
                elif event_direction == "dire":
                    _elo_diff = _winprob.elo_diff(_dt, _rt, _dn, _rn)
            _fair_s3 = _s3_fair(_lead, game.get("game_time_sec"), _elo_diff)
            _edge_s3 = _fair_s3 - executable_price
            if executable_price > S3_MAX_PRICE or _edge_s3 < S3_MIN_EDGE:
                return {
                    "decision": "skip", "reason": "s3_value_edge_too_small",
                    **base_metadata, "fair_price": round(_fair_s3, 4),
                    "executable_price": round(executable_price, 4),
                    "executable_edge": round(_edge_s3, 4),
                    "required_edge": S3_MIN_EDGE, "radiant_lead": _lead,
                }

        if not _is_hold_to_settle and executable_edge < effective_required_edge:
            return {
                "decision": "skip", "reason": "edge_too_small",
                **base_metadata,
                "lag": round(lag, 4), "expected_move": round(expected_move, 4),
                "fair_price": round(fair_price, 4),
                "executable_price": round(executable_price, 4),
                "executable_edge": round(executable_edge, 4),
                "required_edge": round(effective_required_edge, 4),
                "remaining_move": round(remaining_move, 4),
                "market_move_recent": round(market_move, 4),
                "net_edge": round(executable_edge, 4),
                "fair_source": fair_source or ("override" if fair_price_override is not None else "event_model"),
            }

        effective_min_lag = UNDERDOG_REVERSAL_MIN_LAG if is_underdog_reversal else MIN_LAG
        # 2026-05-30 — same logic as edge_too_small bypass: lag is short-horizon
        # markout logic (remaining_move = fair_price - current_price). For
        # hold-to-settle events the per-event MAX_FILL cap (set from settle
        # winrate) is the EV gate; the fair_price-vs-current_price spread is
        # irrelevant because we hold to $0/$1.
        if not _is_hold_to_settle and remaining_move < effective_min_lag:
            return {
                "decision": "skip", "reason": "lag_too_small",
                **base_metadata,
                "lag": round(lag, 4), "expected_move": round(expected_move, 4),
                "fair_price": round(fair_price, 4),
                "executable_price": round(executable_price, 4),
                "executable_edge": round(executable_edge, 4),
                "required_lag": round(effective_min_lag, 4),
                "remaining_move": round(remaining_move, 4),
                "market_move_recent": round(market_move, 4),
                "net_edge": round(executable_edge, 4),
                "fair_source": fair_source or ("override" if fair_price_override is not None else "event_model"),
            }

        recent_price = self._price_n_seconds_ago(token_id, 3)
        if recent_price is not None:
            recent_move = current_price - recent_price
            if recent_move < -MIN_LAG:
                return {
                    "decision": "skip", "reason": "adverse_market_move",
                    **base_metadata,
                    "lag": round(lag, 4), "expected_move": round(expected_move, 4),
                    "market_move_3s": round(recent_move, 4),
                }

        pregame = self._pregame_price.get(token_id)
        pregame_move = (current_price - pregame) if pregame is not None else 0.0

        size_multiplier = min(max(executable_edge, 0.0) / 0.05, MAX_SIZE_MULTIPLIER)
        # 2026-05-31 — Hold-to-settle events don't have a momentum-edge to size on
        # (executable_edge ≈ 0 when holding to $0/$1). Without a floor, size_multiplier
        # collapses to 0 → $0 position. Give them a base of 1.0; the real sizing comes
        # from the event-specific multipliers below (S1 1.6x, mispricing 1.5x, etc).
        from config import EXIT_HORIZON_BY_EVENT as _EH_SZ
        if _EH_SZ.get(primary_event_type, None) == 0:
            size_multiplier = max(size_multiplier, 1.0)
        if pregame is not None and pregame_move > 0.20:
            size_multiplier *= 0.5

        r_score = int(game.get("radiant_score") or 0)
        d_score = int(game.get("dire_score") or 0)
        event_kill_lead = (r_score - d_score) if event_direction == "radiant" else (d_score - r_score)
        if event_kill_lead >= 8:
            size_multiplier = min(size_multiplier * 1.25, MAX_SIZE_MULTIPLIER)
        elif event_kill_lead >= 4:
            size_multiplier = min(size_multiplier * 1.10, MAX_SIZE_MULTIPLIER)

        # 2026-05-31 P4 — Mispricing-tier sizing. When nw direction and kill
        # direction DISAGREE, the entry is exploiting market mispricing (the book
        # is priced on visible kills, net worth says otherwise). Backtest: +$0.124
        # /trade vs +$0.075 on agreement. Size these 1.5x.
        _nw = game.get("radiant_lead")
        if _nw is not None:
            _kd = r_score - d_score
            _nw_dir_rad = float(_nw) > 0
            _kd_dir_rad = _kd > 0
            _nw_kill_disagree = (_nw_dir_rad != _kd_dir_rad) and _kd != 0
            if _nw_kill_disagree and ask < 0.70:
                size_multiplier = min(size_multiplier * 1.5, MAX_SIZE_MULTIPLIER)

        # 2026-05-31 P6 — Confirmation sizing. When 2+ same-direction events fire
        # in this cluster, the signal is higher-conviction (later confirmations
        # scored 96% wr vs 84% for the first event). Scale up.
        _n_confirm = len([e for e in events if _event_attr(e, "event_type") in PRIMARY_TRADE_EVENTS])
        if _n_confirm >= 3:
            size_multiplier = min(size_multiplier * 2.0, MAX_SIZE_MULTIPLIER)
        elif _n_confirm >= 2:
            size_multiplier = min(size_multiplier * 1.5, MAX_SIZE_MULTIPLIER)

        # 2026-05-31 — S1 confidence-weighted sizing (downside protection).
        # The 2 backtest losses both occurred at cheap asks (0.49, 0.58 = near
        # coinflips); wins clustered at 0.65-0.85 (clear favorites). So scale the
        # S1 boost by ask: 0.8x at ask=0.45 → 2.4x at ask=0.85 (linearly). This
        # bets SMALL on uncertain entries (where losses cluster) and BIG on
        # confident ones (where we win). Backtest: total +$393→+$416 AND worst
        # single loss -$46→-$38 — more return AND less risk (confidence-weighting
        # raises both because cheap entries are genuinely lower-edge).
        if primary_event_type == "POLL_FIRST_SWING_SETTLE":
            _conf = 0.5 + 1.0 * (ask - 0.45) / 0.40  # 0.5 at 0.45 → 1.5 at 0.85
            _conf = max(0.4, min(1.6, _conf))
            size_multiplier = min(size_multiplier * 1.6 * _conf, MAX_SIZE_MULTIPLIER)

        target_size_usd = PAPER_TRADE_SIZE_USD * size_multiplier

        cluster_event_types = [_event_attr(e, "event_type") for e in events]
        severities = [_event_attr(e, "severity", "") for e in events]
        trade_score = event_quality * execution_scores["execution_quality_score"]

        return {
            "_cooldown_key": cooldown_key,
            "_cooldown_ms": now_ms,
            "decision": "paper_buy_yes",
            "reason": "event_cluster_lag_signal" if len(events) > 1 else "event_lag_signal",
            "event_type": primary_event_type,
            "event_tier": event_tier(primary_event_type),
            "event_is_primary": event_is_primary(primary_event_type),
            "event_family": event_family(primary_event_type),
            "event_quality": event_quality,
            **_cadence_signal_metadata(events[0]),
            **execution_scores,
            "trade_score": round(trade_score, 4),
            "cluster_event_types": "+".join(cluster_event_types),
            "event_direction": event_direction,
            "token_id": token_id,
            "side": "YES" if event_favors_yes else "NO",
            "lag": round(lag, 4),
            "expected_move": round(expected_move, 4),
            "fair_price": round(fair_price, 4),
            "executable_price": round(executable_price, 4),
            "executable_edge": round(executable_edge, 4),
            "required_edge": round(required_edge, 4),
            "remaining_move": round(remaining_move, 4),
            "market_move_recent": round(market_move, 4),
            "price_lookback_sec": PRICE_LOOKBACK_SEC,
            "pregame_move": round(pregame_move, 4) if pregame is not None else None,
            "anchor_price": round(anchor_price, 4),
            "current_price": round(current_price, 4),
            "ask": ask,
            "max_fill_price": round(max_fill, 4),
            "bid": float(bid) if bid is not None else None,
            "spread": round(spread, 4) if spread is not None else None,
            "ask_size": ask_size,
            "target_size_usd": round(target_size_usd, 2),
            "size_multiplier": round(size_multiplier, 2),
            "phase_mult": time_multiplier(game_time),
            "event_kill_lead": event_kill_lead,
            "fair_source": fair_source or ("override" if fair_price_override is not None else "event_model"),
            "severity": "+".join([s for s in severities if s]),
            "game_time_sec": game_time,
            "estimated_game_time_sec": round(game_time + steam_age / 1000, 1) if game_time is not None and steam_age is not None else None,
            "steam_age_ms": steam_age,
            "source_update_age_sec": round(source_update_age_sec, 3) if source_update_age_sec is not None else None,
            "stream_delay_s": round(stream_delay_s, 3) if stream_delay_s is not None else None,
            "data_source": data_source,
            "book_age_ms": book_age,
            "book_age_at_signal_ms": book_age,
            "structure_uncertainty_penalty": round(structure_uncertainty_penalty, 4),
            **market_scope_metadata(mapping),
            "series_score_yes": mapping.get("series_score_yes"),
            "series_score_no": mapping.get("series_score_no"),
            "current_game_number": mapping.get("current_game_number") or mapping.get("game_number"),
            "is_underdog_reversal": _is_underdog_reversal,
            "series_type": mapping.get("series_type"),
        }

    def _adjusted_event_value(self, event: Any, game: dict) -> float:
        event_type = _event_attr(event, "event_type")
        spec = ACTIVE_EVENTS[event_type]
        game_time = game.get("game_time_sec")
        value = spec.base * time_multiplier(game_time)
        
        # For structure/base events, use structure_confidence as a probability multiplier.
        if event_type in {
            "OBJECTIVE_CONVERSION_T2", "OBJECTIVE_CONVERSION_T3", "OBJECTIVE_CONVERSION_T4",
            "THRONE_EXPOSED", "BASE_PRESSURE_T4", "BASE_PRESSURE_T3_COLLAPSE",
        }:
            struct_conf = _event_attr(event, "structure_confidence")
            if struct_conf is not None:
                value *= float(struct_conf)

        event_game_time = _event_attr(event, "game_time_sec", game_time)
        age_sec = 0.0
        if game_time is not None and event_game_time is not None:
            age_sec = max(0.0, float(game_time) - float(event_game_time))
        value *= freshness_multiplier(age_sec, spec.half_life_sec)

        delta = _event_attr(event, "delta")
        if delta is not None and isinstance(delta, (int, float)):
            abs_delta = abs(float(delta))
            if event_type == "POLL_COMEBACK_RECOVERY":
                value *= min(abs_delta / 1800, 2.0)
            elif event_type == "POLL_MAJOR_COMEBACK_RECOVERY":
                value *= min(abs_delta / 3500, 2.0)
            elif event_type == "POLL_KILL_BURST_CONFIRMED":
                value *= min(abs_delta / _KILL_BURST_MIN, 2.0)
            elif event_type == "POLL_FIGHT_SWING":
                gold_mult = min(abs_delta / 1000.0, 2.0)
                abs_kills = abs(float(_event_attr(event, "kill_diff_delta") or 0))
                kill_boost = min(1.0 + abs_kills * 0.12, 1.6)
                # Game kill count scale: low-kill games repress small (avg|fwd30|=0.019 at <5 kills).
                # Scale up toward 1.0 as total_kills grows; plateaus at 30+ kills.
                total_kills = (game.get("radiant_score") or 0) + (game.get("dire_score") or 0)
                kill_count_scale = min(float(total_kills) / 20.0, 1.0)
                value *= gold_mult * kill_boost * kill_count_scale
            elif event_type == "POLL_LEAD_FLIP_WITH_KILLS":
                value *= min(abs_delta / 1500.0, 2.0)
            elif event_type == "POLL_STOMP_THROW_CONFIRMED":
                value *= min(abs_delta / 2500.0, 2.0)
            elif event_type == "POLL_LATE_FIGHT_FLIP":
                value *= min(abs_delta / 2500.0, 2.0)
            elif event_type == "POLL_ULTRA_LATE_FIGHT_FLIP":
                value *= min(abs_delta / 3000.0, 2.0)
            elif event_type in {
                "OBJECTIVE_CONVERSION_T2", "OBJECTIVE_CONVERSION_T3", "OBJECTIVE_CONVERSION_T4",
                "THRONE_EXPOSED", "BASE_PRESSURE_T4", "BASE_PRESSURE_T3_COLLAPSE",
            }:
                if abs_delta > 1:
                    value *= min(abs_delta, 2.0)

        cadence_quality = _event_attr(event, "source_cadence_quality")
        if cadence_quality == "stale_gap":
            value *= 0.75
        elif cadence_quality == "invalid_gap":
            value = 0.0

        # Lead-Scaled Move Impact: Scaling the expected move based on total lead.
        # Moves extending a lead are more decisive than moves clawing back.
        event_direction = _event_attr(event, "direction")
        if event_direction:
            team_lead = _event_team_lead(event_direction, game)
            if team_lead is not None:
                # Factor: 0.8 at parity ($0), 1.0 at $5k lead, 1.2 at $15k+ lead.
                lead_mult = min(1.2, 0.8 + (max(0, team_lead) / 25000.0))

                # Deficit Dampening: If still far behind, reduce impact.
                if team_lead < -5000:
                    lead_mult = max(0.2, 0.8 + (team_lead / 10000.0))

                value *= lead_mult

        return min(value, spec.cap)

    @staticmethod
    def _combine_event_impacts(values: list[float]) -> float:
        if not values:
            return 0.0
        values = sorted(values, reverse=True)
        return values[0] + 0.25 * sum(values[1:])

    @staticmethod
    def _state_cap(events: Iterable[Any], game: dict) -> float:
        event_types = {_event_attr(e, "event_type") for e in events}
        if "THRONE_EXPOSED" in event_types:
            return 0.70
        if "OBJECTIVE_CONVERSION_T4" in event_types:
            return 0.65
        if "BASE_PRESSURE_T4" in event_types:
            return 0.50
        if "POLL_ULTRA_LATE_FIGHT_FLIP" in event_types:
            return 0.52
        if "OBJECTIVE_CONVERSION_T3" in event_types:
            return 0.42
        if "POLL_STOMP_THROW_CONFIRMED" in event_types:
            return 0.40
        if "POLL_LATE_FIGHT_FLIP" in event_types:
            return 0.45
        if "POLL_LEAD_FLIP_WITH_KILLS" in event_types or "POLL_MAJOR_COMEBACK_RECOVERY" in event_types:
            return 0.40
        if "BASE_PRESSURE_T3_COLLAPSE" in event_types:
            return 0.32
        if "POLL_KILL_BURST_CONFIRMED" in event_types:
            return 0.28
        if "POLL_FIGHT_SWING" in event_types:
            return 0.24
        if "POLL_COMEBACK_RECOVERY" in event_types:
            return 0.22
        if "OBJECTIVE_CONVERSION_T2" in event_types:
            return 0.20
        game_time = game.get("game_time_sec")
        if game_time is not None and game_time >= 2400:
            return 0.22
        return 0.12

    @staticmethod
    def _context_multiplier(events: Iterable[Any], game: dict) -> float:
        """Risk-adjust the probability shock using only same-feed context.

        Tower-only moves can be split-push noise; objective-conversion events are
        stronger because the tower fall was accompanied by same-direction kills or
        net worth. Wipes/comebacks are already strong and should not be damped.
        """
        event_list = list(events)
        event_types = {_event_attr(e, "event_type") for e in event_list}
        event_direction = _event_attr(event_list[0], "direction", "") if event_list else ""
        ctx = _structure_context(event_direction, game) if event_direction else None
        event_team_lead = _event_team_lead(event_direction, game) if event_direction else None

        mult = 1.0
        if any(e.startswith("OBJECTIVE_CONVERSION_") for e in event_types):
            mult *= 1.08
            if ctx and (ctx.get("enemy_t3_dead", 0) >= 2 or ctx.get("enemy_t4_dead", 0) >= 1):
                mult *= 1.04
            if event_team_lead is not None and event_team_lead >= 8000:
                mult *= 1.03
            return min(mult, 1.18)

        raw_structure = event_types & {"BASE_PRESSURE_T3_COLLAPSE", "BASE_PRESSURE_T4", "THRONE_EXPOSED"}
        support = event_types & {
            "POLL_COMEBACK_RECOVERY", "POLL_MAJOR_COMEBACK_RECOVERY",
            "POLL_LEAD_FLIP_WITH_KILLS", "POLL_KILL_BURST_CONFIRMED",
            "POLL_FIGHT_SWING", "POLL_LATE_FIGHT_FLIP",
            "POLL_ULTRA_LATE_FIGHT_FLIP", "POLL_STOMP_THROW_CONFIRMED",
        }
        if raw_structure and not support:
            mult *= 0.82
        comeback_like = {
            "POLL_COMEBACK_RECOVERY",
            "POLL_MAJOR_COMEBACK_RECOVERY",
            "POLL_LEAD_FLIP_WITH_KILLS",
            "POLL_STOMP_THROW_CONFIRMED",
            "POLL_LATE_FIGHT_FLIP",
            "POLL_ULTRA_LATE_FIGHT_FLIP",
        }
        if event_team_lead is not None and event_team_lead < -4000 and not (event_types & comeback_like):
            mult *= 0.92
        return min(max(mult, 0.75), 1.15)

    @staticmethod
    def _required_edge(events: Iterable[Any], game: dict, ask: float, spread: float | None) -> float:
        """Dynamic edge buffer for live-fill noise and model uncertainty."""
        required = MIN_EXECUTABLE_EDGE
        event_list = list(events)
        event_types = {_event_attr(e, "event_type") for e in event_list}
        event_direction = _event_attr(event_list[0], "direction", "") if event_list else ""
        event_team_lead = _event_team_lead(event_direction, game) if event_direction else None

        if any(e.startswith("OBJECTIVE_CONVERSION_") for e in event_types):
            required += 0.005
        if event_types & {"POLL_STOMP_THROW_CONFIRMED", "POLL_ULTRA_LATE_FIGHT_FLIP", "POLL_LATE_FIGHT_FLIP"}:
            required += 0.02
        if event_types & {"POLL_DECISIVE_STOMP", "POLL_RAPID_STOMP"}:
            # Stomp context: market near terminal price, require extra edge to trade primary signals
            required += 0.02
        if event_types & {"POLL_COMEBACK_RECOVERY", "POLL_MAJOR_COMEBACK_RECOVERY", "POLL_LEAD_FLIP_WITH_KILLS"}:
            required += 0.01
        if event_types == {"OBJECTIVE_CONVERSION_T2"}:
            required += 0.015
        if spread is not None and spread > MAX_SPREAD * 0.5:
            required += 0.005
        if ask >= 0.75:
            required += 0.005
        if event_team_lead is not None:
            comeback_like = {
                "POLL_COMEBACK_RECOVERY",
                "POLL_MAJOR_COMEBACK_RECOVERY",
                "POLL_LEAD_FLIP_WITH_KILLS",
                "POLL_STOMP_THROW_CONFIRMED",
                "POLL_LATE_FIGHT_FLIP",
                "POLL_ULTRA_LATE_FIGHT_FLIP",
            }
            if event_team_lead < -4000 and not (event_types & comeback_like):
                required += 0.010
            elif event_team_lead >= 10000 and any(e.startswith("OBJECTIVE_CONVERSION_") or e == "BASE_PRESSURE_T4" for e in event_types):
                required -= 0.003
        required = max(MIN_EXECUTABLE_EDGE, required)

        game_time = game.get("game_time_sec")
        if game_time is not None and game_time >= 45 * 60 and not (event_types & {"POLL_ULTRA_LATE_FIGHT_FLIP", "BASE_PRESSURE_T4", "THRONE_EXPOSED", "OBJECTIVE_CONVERSION_T4"}):
            required += 0.005
        if "POLL_FIGHT_SWING" in event_types and game_time is not None and game_time >= 2400:
            required += 0.02
        return required

    def commit_signal(self, signal: dict) -> None:
        key = signal.get("_cooldown_key")
        ms = signal.get("_cooldown_ms")
        if key and ms:
            self._last_signal_ms[key] = ms
