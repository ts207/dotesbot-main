"""NO-MODEL event strategy — replaces signal_engine for entry decisions.

Pure rule-based BUY signals built from the live audit (see signal_quality_audit.py):

  REJECT IF:
    - spread > 0.04                           (audit: -4.5c avg, 33% win)
    - 0.45 <= price <= 0.70                   (audit: -6c avg toss-up zone)
    - event_type not in whitelist
    - game_time < 900s                        (audit: mid-game noise dominates)
    - kill_delta < 2 AND networth_delta < 2000 (event has no real evidence)

  SIZE:
    base = $50
    × 1.5 if kill_diff_delta >= 3
    × 2.0 if (premium event match: LATE_FIGHT_FLIP+conf>=0.9,
              VALUE_DISAGREE+nw>=2000, KILL_BURST+nw>=5000)
    × 3.0 if kill_diff_delta >= 5            (extreme momentum)

  EXPECTED MOVE (lookup-table, replaces fair_price):
    Per (event, kill_delta_bucket) → empirical_move from audit data.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

# ---------- Whitelist ----------
NOMODEL_TIER_A = frozenset({
    "POLL_KILL_BURST_CONFIRMED",
    "POLL_LATE_FIGHT_FLIP",
    "POLL_VALUE_DISAGREEMENT",
    "POLL_COMEBACK_RECOVERY",
    "POLL_STRUCTURAL_DOMINANCE",
    "OBJECTIVE_CONVERSION_T2",
    "POLL_BUYBACK_CAPITULATION",
})
NOMODEL_TIER_B = frozenset({
    "POLL_FIGHT_SWING",      # marginal +0.6c
    "POLL_DECISIVE_STOMP",   # marginal +0.16c
})
NOMODEL_WHITELIST = NOMODEL_TIER_A | NOMODEL_TIER_B

# Confirmed anti-signal in live shadow audit
NOMODEL_BLACKLIST = frozenset({
    "POLL_STOMP_THROW_CONFIRMED",      # -8.2c, 43% win
    "POLL_LEAD_FLIP_WITH_KILLS",       # -3c, 40% win
    "POLL_MAJOR_COMEBACK_RECOVERY",    # 0c, 49% win (high variance)
    "POLL_RAPID_STOMP",                # -0.1c, 59% win (flat)
    "POLL_ULTRA_LATE_FIGHT_FLIP",      # n=6 0c
    "BLOODY_EVEN_FIGHT",               # -1.7c, 33% win
})

# Empirical expected move per (event, condition) — used in lieu of model fair_price
# Values: ¢ above entry on the YES side IF event.direction matches.
# Source: signal_quality_audit.py RANK 1 + RANK 5
EXPECTED_MOVE_TABLE = {
    # event_type → list of (condition_fn, expected_move_cents)
    "POLL_LATE_FIGHT_FLIP": lambda ev: 0.137 if ev.get("event_confidence", 0) >= 0.9 else 0.049,
    "POLL_VALUE_DISAGREEMENT": lambda ev: 0.083 if abs(ev.get("networth_delta", 0)) >= 2000 else 0.029,
    "POLL_KILL_BURST_CONFIRMED": lambda ev: 0.034 if abs(ev.get("networth_delta", 0)) >= 5000 else 0.021,
    "POLL_COMEBACK_RECOVERY": lambda ev: 0.034 if abs(ev.get("networth_delta", 0)) >= 2000 else 0.023,
    "POLL_STRUCTURAL_DOMINANCE": lambda ev: 0.007,
    "OBJECTIVE_CONVERSION_T2": lambda ev: 0.023,
    "POLL_BUYBACK_CAPITULATION": lambda ev: 0.040,
    "POLL_FIGHT_SWING": lambda ev: 0.006,
    "POLL_DECISIVE_STOMP": lambda ev: 0.002,
}

# Per-event empirical exit horizon (seconds). 0 = hold to settle.
# From audit + EXIT_HORIZON_BY_EVENT in config.
HORIZON_BY_EVENT = {
    "POLL_KILL_BURST_CONFIRMED": 90,
    "POLL_LATE_FIGHT_FLIP": 90,
    "POLL_VALUE_DISAGREEMENT": 0,      # hold to settle
    "POLL_STRUCTURAL_DOMINANCE": 0,    # hold to settle
    "POLL_COMEBACK_RECOVERY": 60,
    "OBJECTIVE_CONVERSION_T2": 60,
    "POLL_BUYBACK_CAPITULATION": 0,
    "POLL_FIGHT_SWING": 120,
    "POLL_DECISIVE_STOMP": 0,
}

# ---------- Gate thresholds ----------
# 2026-05-27 — Option C (pure whitelist): dropped price + kill_delta gates,
# relaxed spread + game_time. Audit-justified blacklist + spread + early-game
# skip are the ONLY filters.
NOMODEL_MAX_SPREAD = float(os.getenv("NOMODEL_MAX_SPREAD", "0.05"))      # was 0.04
NOMODEL_TOSS_LOW = float(os.getenv("NOMODEL_TOSS_LOW", "1.01"))           # was 0.45 (disabled: >1.0 → never triggers)
NOMODEL_TOSS_HIGH = float(os.getenv("NOMODEL_TOSS_HIGH", "1.01"))         # was 0.70 (disabled)
NOMODEL_MIN_GAME_TIME = float(os.getenv("NOMODEL_MIN_GAME_TIME", "300"))  # was 900 (5 min, not 15)
NOMODEL_MIN_KILL_DELTA = int(os.getenv("NOMODEL_MIN_KILL_DELTA", "0"))    # was 2 (disabled)
NOMODEL_MIN_NW_DELTA = int(os.getenv("NOMODEL_MIN_NW_DELTA", "0"))        # was 2000 (disabled)

# ---------- Sizing ----------
NOMODEL_BASE_STAKE_USD = float(os.getenv("NOMODEL_BASE_STAKE_USD", "50"))
NOMODEL_PREMIUM_MULT = float(os.getenv("NOMODEL_PREMIUM_MULT", "2.0"))
NOMODEL_KILL_DELTA_3_MULT = float(os.getenv("NOMODEL_KILL_DELTA_3_MULT", "1.5"))
NOMODEL_KILL_DELTA_5_MULT = float(os.getenv("NOMODEL_KILL_DELTA_5_MULT", "3.0"))

# ---------- Per-match cap (2026-05-27) ----------
# Audit: 1 match alone lost $1.19 across 7 trades (12% of all trades, 119%
# of total losses). Cap to NOMODEL_MAX_TRADES_PER_MATCH to prevent loss
# concentration on bad matches.
NOMODEL_MAX_TRADES_PER_MATCH = int(os.getenv("NOMODEL_MAX_TRADES_PER_MATCH", "2"))

# Module-level counter keyed by match_id. Incremented when this evaluator
# returns decision="buy". Caller is responsible for calling note_buy_placed()
# to ensure we only count actual entries (not skips).
_trades_per_match: dict[str, int] = {}


def note_buy_placed(match_id: str) -> None:
    """Called by main.py after a nomodel buy signal is sent to live_executor.
    Increments the per-match counter so subsequent calls hit the cap."""
    if not match_id:
        return
    _trades_per_match[match_id] = _trades_per_match.get(match_id, 0) + 1


def trades_on_match(match_id: str) -> int:
    return _trades_per_match.get(str(match_id), 0)


def _is_premium(event: dict) -> bool:
    et = event.get("event_type")
    if et == "POLL_LATE_FIGHT_FLIP" and event.get("event_confidence", 0) >= 0.9: return True
    if et == "POLL_VALUE_DISAGREEMENT" and abs(event.get("networth_delta", 0)) >= 2000: return True
    if et == "POLL_KILL_BURST_CONFIRMED" and abs(event.get("networth_delta", 0)) >= 5000: return True
    return False


def evaluate_event_nomodel(
    *, event: dict, game: dict, mapping: dict,
    yes_book: dict | None, no_book: dict | None,
) -> dict[str, Any]:
    """Build a BUY/SKIP decision from pure rules — no model.

    Returns a dict matching the shape signal_engine.evaluate_cluster() produces,
    so live_executor.try_buy() can consume it unchanged.
    """
    et = event.get("event_type") or ""
    direction = (event.get("direction") or event.get("event_direction") or "").lower()
    match_id = str(game.get("match_id") or game.get("lobby_id") or "")

    # Gate 0: per-match trade cap (audit-driven — single match lost $1.19 with 7 trades)
    if match_id and trades_on_match(match_id) >= NOMODEL_MAX_TRADES_PER_MATCH:
        return {"decision": "skip",
                "skip_reason": f"nomodel_match_cap:{trades_on_match(match_id)}/{NOMODEL_MAX_TRADES_PER_MATCH}"}

    # Gate 1: blacklist
    if et in NOMODEL_BLACKLIST:
        return {"decision": "skip", "skip_reason": f"nomodel_blacklist:{et}"}

    # Gate 2: whitelist
    if et not in NOMODEL_WHITELIST:
        return {"decision": "skip", "skip_reason": f"nomodel_not_whitelisted:{et}"}

    # Gate 3: direction known
    if direction not in ("radiant", "dire"):
        return {"decision": "skip", "skip_reason": "nomodel_no_direction"}

    # Pick side based on direction matching market
    yes_is_radiant = (
        (mapping.get("yes_team") or "").lower() == (mapping.get("steam_radiant_team") or "").lower()
        and mapping.get("steam_radiant_team")
    )
    if yes_is_radiant:
        side = "YES" if direction == "radiant" else "NO"
    else:
        side = "NO" if direction == "radiant" else "YES"
    token_id = mapping.get("yes_token_id") if side == "YES" else mapping.get("no_token_id")
    book = yes_book if side == "YES" else no_book

    # Gate 4: have book
    if book is None or book.get("best_ask") is None or book.get("best_bid") is None:
        return {"decision": "skip", "skip_reason": "nomodel_missing_book"}

    bid = float(book["best_bid"]); ask = float(book["best_ask"])

    # Gate 5: spread
    spread = ask - bid
    if spread > NOMODEL_MAX_SPREAD:
        return {"decision": "skip", "skip_reason": f"nomodel_spread_{spread:.3f}_over_{NOMODEL_MAX_SPREAD}"}

    # Gate 6: price not in toss-up zone
    if NOMODEL_TOSS_LOW <= ask < NOMODEL_TOSS_HIGH:
        return {"decision": "skip", "skip_reason": f"nomodel_toss_up_zone:ask={ask:.3f}"}

    # Gate 7: game time
    gt = game.get("game_time_sec") or 0
    if gt < NOMODEL_MIN_GAME_TIME:
        return {"decision": "skip", "skip_reason": f"nomodel_early_game:gt={gt}"}

    # Gate 8: event has actual evidence (kill delta OR networth delta)
    kd = abs(event.get("kill_diff_delta") or 0)
    nwd = abs(event.get("networth_delta") or 0)
    if kd < NOMODEL_MIN_KILL_DELTA and nwd < NOMODEL_MIN_NW_DELTA:
        return {"decision": "skip", "skip_reason": f"nomodel_no_evidence:kd={kd}_nwd={nwd}"}

    # Sizing
    stake = NOMODEL_BASE_STAKE_USD
    size_mult = 1.0
    if kd >= 5: size_mult = NOMODEL_KILL_DELTA_5_MULT
    elif kd >= 3: size_mult = NOMODEL_KILL_DELTA_3_MULT
    if _is_premium(event): size_mult = max(size_mult, NOMODEL_PREMIUM_MULT)
    stake *= size_mult

    # Expected move (lookup, used in lieu of fair_price)
    expected_move_cents = EXPECTED_MOVE_TABLE.get(et, lambda e: 0.02)(event)
    fair_price = ask + expected_move_cents
    fair_price = min(fair_price, 0.99)

    # Build signal in the shape expected by live_executor.try_buy()
    return {
        "decision": "buy",
        "skip_reason": None,
        "event_type": et,
        "event_direction": direction,
        "event_tier": "A" if et in NOMODEL_TIER_A else "B",
        "event_is_primary": True,
        "event_family": "nomodel",
        "event_quality": float(event.get("event_quality", 0.5) or 0.5),
        "cluster_event_types": et,
        "token_id": token_id,
        "side": side,
        "ask": ask,
        "bid": bid,
        "spread": spread,
        "fair_price": fair_price,
        "executable_edge": expected_move_cents,
        "remaining_move": expected_move_cents,
        "lag": 0.10,  # nominal — not used by nomodel
        "expected_move": expected_move_cents,
        "size_multiplier": size_mult,
        "target_size_usd": stake,
        "severity": str(event.get("severity") or ""),
        "networth_delta": event.get("networth_delta"),
        "kill_diff_delta": event.get("kill_diff_delta"),
        "event_confidence": event.get("event_confidence"),
        "event_schema_version": event.get("event_schema_version") or "cadence_v1",
        "source_cadence_quality": event.get("source_cadence_quality") or "normal",
        "fair_source": "nomodel_lookup",
        "is_underdog_reversal": False,
        # The exit horizon is consumed by live_exit_engine via EXIT_HORIZON_BY_EVENT;
        # we surface it here for telemetry only.
        "exit_horizon_sec": HORIZON_BY_EVENT.get(et, 120),
    }
