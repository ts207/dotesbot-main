from __future__ import annotations

import os
import time
from dataclasses import dataclass

import winprob
from config import (
    EXIT_TAKE_PROFIT,
    EXIT_STOP_LOSS_ABS,
    EXIT_STOP_LOSS_REL,
    EXIT_LATENCY_EDGE_SEC,
    EXIT_HORIZON_SEC,
    EXIT_HORIZON_BY_EVENT,
    MAX_HOLD_HOURS,
    UNDERDOG_REVERSAL_TAKE_PROFIT,
    UNDERDOG_REVERSAL_STOP_ABS,
    UNDERDOG_REVERSAL_LEAD_THRESHOLD,
    EXIT_TRAILING_STOP_CENTS,
    EXIT_TRAILING_STOP_GRACE_SEC,
    VALUE_EXIT_FAIR_INVALIDATION_ENABLED,
    VALUE_EXIT_FAIR_ENTRY_BUFFER,
    VALUE_EXIT_FAIR_BID_BUFFER,
)

# Catastrophe-salvage floor for hold-to-settle positions: if the token bid falls
# below this, cut to salvage residual value instead of riding to $0. Keep LOW so
# recoverable dips aren't stopped. 0 disables.
CATASTROPHE_FLOOR = float(os.getenv("CATASTROPHE_FLOOR", "0.12"))
# Our backed side must be behind by at least this (net worth) to CONFIRM a
# catastrophe cut — so a cheap bid alone (possible flip/glitch) can't dump a winner.
CATASTROPHE_NW_CONFIRM = float(os.getenv("CATASTROPHE_NW_CONFIRM", "2000"))


@dataclass(frozen=True)
class ExitDecision:
    should_exit: bool
    reason: str = ""
    reference_bid: float | None = None
    price_floor: float | None = None


def decide_live_exit(
    *,
    position,
    book: dict | None,
    game_over_match_ids: set[str],
    adverse_token_ids: set[str] | None = None,
    now_ns: int | None = None,
    game: dict | None = None,
) -> ExitDecision:
    now_ns = now_ns or time.time_ns()
    adverse_token_ids = adverse_token_ids or set()

    trader_kind = getattr(position, "trader_kind", "event")

    if trader_kind not in ("value", "dswing") and position.token_id in adverse_token_ids:
        return ExitDecision(True, "adverse_event")

    if trader_kind == "dswing":
        return _decide_dswing_exit(
            position=position,
            book=book,
            game_over_match_ids=game_over_match_ids,
            now_ns=now_ns,
        )
    # 2026-06-03 — winprob VALUE bot: TRUE hold-to-settle. The edge is
    # informational (net worth predicts winner); selling early underperforms
    # (proven: every active-exit mechanism loses to hold-to-settle). Exit ONLY at
    # game settlement or the max-hold safety net; immune to adverse moves (above).
    if trader_kind == "value":
        raw_bid = (book or {}).get("best_bid")
        bid = float(raw_bid) if raw_bid is not None else None
        if position.match_id in game_over_match_ids:
            return ExitDecision(True, "game_over", bid)
        # CATASTROPHE SALVAGE: if the market has repriced our token near-zero the
        # bet has gone irreversibly wrong (true reversal or a flip that slipped the
        # entry guard) — cut it to salvage residual value instead of riding to $0.
        # Floor is LOW (default 0.12) so it never stops a recoverable dip — the
        # validated hold-to-settle edge is preserved; this only fires on a genuine
        # near-total loss. (Disable with CATASTROPHE_FLOOR=0.)
        if bid is not None and 0.0 < CATASTROPHE_FLOOR and bid < CATASTROPHE_FLOOR:
            # Cross-check the cheap bid against the LIVE Dota match state — only
            # salvage-cut if net worth CONFIRMS our backed side is losing. If net
            # worth says we're winning despite the cheap bid, it's a flip/market
            # glitch → DON'T dump a winner (hold). Fall back to price-only when the
            # match state isn't available (old positions / no live game snapshot).
            bd = getattr(position, "backed_direction", None)
            rl = None
            if game is not None:
                try:
                    rl = int(game.get("radiant_lead"))
                except (TypeError, ValueError):
                    rl = None
            if bd in ("radiant", "dire") and rl is not None:
                backed_lead = rl if bd == "radiant" else -rl
                if backed_lead < -CATASTROPHE_NW_CONFIRM:
                    return ExitDecision(True, "catastrophe_salvage", bid)
                # cheap bid but net worth says we're NOT losing → flip/glitch: hold
            else:
                return ExitDecision(True, "catastrophe_salvage", bid)  # no state → price-only
        if (
            VALUE_EXIT_FAIR_INVALIDATION_ENABLED
            and bid is not None
            and game is not None
        ):
            current_fair = _current_fair_for_position(position, game)
            if (
                current_fair is not None
                and current_fair < position.entry_price - VALUE_EXIT_FAIR_ENTRY_BUFFER
                and current_fair < bid - VALUE_EXIT_FAIR_BID_BUFFER
            ):
                return ExitDecision(True, "fair_invalidation", bid, current_fair)
        age_sec = (now_ns - position.entry_time_ns) / 1e9
        if age_sec >= MAX_HOLD_HOURS * 3600:
            return ExitDecision(True, "max_hold_timeout", None)
        return ExitDecision(False)

    is_underdog = getattr(position, "is_underdog_reversal", False)

    if is_underdog:
        return _decide_underdog_exit(
            position=position,
            book=book,
            game_over_match_ids=game_over_match_ids,
            game=game,
            now_ns=now_ns,
        )

    raw_bid = (book or {}).get("best_bid")
    bid = float(raw_bid) if raw_bid is not None else None

    age_sec = (now_ns - position.entry_time_ns) / 1e9

    if position.match_id in game_over_match_ids:
        return ExitDecision(True, "game_over", bid)

    if bid is None:
        if age_sec >= MAX_HOLD_HOURS * 3600:
            return ExitDecision(True, "max_hold_timeout", None)
        return ExitDecision(False)

    model_target = position.fair_price if position.fair_price > position.entry_price else None
    if model_target is None and position.expected_move > 0:
        model_target = position.entry_price + position.expected_move

    take_profit_price = min(model_target or EXIT_TAKE_PROFIT, EXIT_TAKE_PROFIT)

    # Calculate current spread if possible
    raw_ask = (book or {}).get("best_ask")
    ask = float(raw_ask) if raw_ask is not None else None
    current_spread = (ask - bid) if (ask is not None and bid is not None) else 0.0

    # Stop-loss calculation:
    # Use the larger of (spread + 2c buffer) or the default EXIT_STOP_LOSS_REL.
    # This prevents the bot from immediately exiting just because it bought at the Ask.
    dynamic_stop_offset = max(EXIT_STOP_LOSS_REL, current_spread + 0.02)
    
    stop_offset = (
        min(dynamic_stop_offset, position.expected_move)
        if position.expected_move > dynamic_stop_offset # Only cap if expected move is large
        else dynamic_stop_offset
    )
    # Final safety: stop_price must be at least 2c below current bid if bid exists
    calculated_stop = position.entry_price - stop_offset
    bid_safety_stop = (bid - 0.02) if bid is not None else 0.0
    stop_price = max(EXIT_STOP_LOSS_ABS, min(calculated_stop, bid_safety_stop))

    event_horizon = EXIT_HORIZON_BY_EVENT.get(position.event_type, EXIT_HORIZON_SEC)
    # horizon == 0 explicitly means "hold to settlement". For those events,
    # disabling model_value_exit lets the price drift past fair_price toward 1.0
    # as GG consensus forms. B4 backtest: POLL_FIGHT_SWING realized 64% of settle
    # PnL with model_value_exit enabled, 100% with it disabled.
    hold_to_settle = (event_horizon == 0)

    # 2026-06-02 — TRUE HOLD-TO-SETTLE. The validated strategy (Option 3, 84%/
    # +0.116/$1) holds every position to SETTLEMENT with NO active exits — and
    # the bot's own backtests + [[feedback-failed-strategies]] showed every exit
    # mechanism (take_profit, stop_loss, hard_stop, trailing, model_value)
    # UNDERPERFORMS hold-to-settle: they sell the 84% winners early and only help
    # on the 16% losers, net-negative. Previously only model_value_exit + horizon
    # were gated on hold_to_settle, so TP/stop/hard_stop/trailing still fired and
    # silently turned the validated edge into an untested scalping strategy (this
    # sold the first live LGD position at 0.68 via model_value_exit after a
    # reconcile). For hold_to_settle events: exit ONLY at game settlement
    # (game_over, handled above) or the max-hold safety. Nothing else.
    if hold_to_settle:
        if age_sec >= MAX_HOLD_HOURS * 3600:
            return ExitDecision(True, "max_hold_timeout", bid)
        return ExitDecision(False)

    if bid >= take_profit_price:
        return ExitDecision(True, "take_profit", bid)
    # Swing-profit exit (added 2026-05-26): if we bought an underdog (entry < 0.50)
    # and the price has bounced ≥ 20¢ above entry, lock in the swing profit
    # rather than holding to settle. Catches the Tundra/Aurora pattern: buy
    # cheap on comeback, exit on the bounce before the next reversal.
    if position.entry_price < 0.50 and bid >= position.entry_price + 0.20:
        return ExitDecision(True, "swing_take_profit", bid)
    if not hold_to_settle and position.fair_price > 0 and bid >= position.fair_price:
        return ExitDecision(True, "model_value_exit", bid)
    if bid <= stop_price:
        # Flash-crash guard: hold through dips <25c in the first 30s without game_over.
        flash_drop = position.entry_price - bid
        is_flash = flash_drop < 0.25 and age_sec < 30 and position.match_id not in game_over_match_ids
        if not is_flash:
            return ExitDecision(True, "stop_loss", bid)
    # 2026-05-27 HARD STOP-LOSS (independent of model). Always cuts at
    # entry - HARD_STOP_CENTS regardless of fair_price / expected_move.
    # Caps the worst-case loss on any signal where the model holds too long.
    import os as _os
    HARD_STOP_CENTS = float(_os.getenv("EXIT_HARD_STOP_LOSS_CENTS", "0.15"))
    if HARD_STOP_CENTS > 0 and bid <= position.entry_price - HARD_STOP_CENTS:
        # No flash guard here — this is the LAST resort. Bigger size at risk
        # = bigger guarantee we exit.
        return ExitDecision(True, "hard_stop_loss", bid)
    if (EXIT_TRAILING_STOP_CENTS > 0
          and age_sec >= EXIT_TRAILING_STOP_GRACE_SEC
          and getattr(position, "peak_bid", 0.0) > position.entry_price
          and bid <= getattr(position, "peak_bid", 0.0) - EXIT_TRAILING_STOP_CENTS):
        return ExitDecision(True, "trailing_stop", bid)
    if EXIT_LATENCY_EDGE_SEC > 0 and age_sec >= EXIT_LATENCY_EDGE_SEC:
        return ExitDecision(True, "latency_edge_timeout", bid)
    if event_horizon > 0 and age_sec >= event_horizon:
        return ExitDecision(True, "horizon", bid)
    if age_sec >= MAX_HOLD_HOURS * 3600:
        return ExitDecision(True, "max_hold_timeout", bid)

    return ExitDecision(False)


from fair_value import compute_side_fair

def _current_fair_for_position(position, game: dict) -> float | None:
    backed = getattr(position, "backed_direction", None) or getattr(position, "entry_backed_side", None)
    if backed not in ("radiant", "dire"):
        return None
    try:
        radiant_lead = int(float(game.get("radiant_lead")))
    except (TypeError, ValueError):
        return None
        
    fair_res = compute_side_fair(game=game, side=backed, record_history=False)
    return fair_res.fair


def _decide_dswing_exit(
    *,
    position,
    book: dict | None,
    game_over_match_ids: set[str],
    now_ns: int,
) -> ExitDecision:
    """DSWING is a map-end convergence trade, not a settlement hold.

    For non-decider MATCH_WINNER markets the token does not redeem at map end,
    so unlike VALUE we must sell when the map ends and the ML book reprices.
    No active stops or take-profit until then; the entry edge was validated as a
    convergence hold.
    """
    raw_bid = (book or {}).get("best_bid")
    bid = float(raw_bid) if raw_bid is not None else None
    age_sec = (now_ns - position.entry_time_ns) / 1e9

    if position.match_id in game_over_match_ids:
        return ExitDecision(True, "map_end_convergence", bid)

    if age_sec >= MAX_HOLD_HOURS * 3600:
        return ExitDecision(True, "max_hold_timeout", bid)

    return ExitDecision(False)


def _decide_underdog_exit(
    *,
    position,
    book: dict | None,
    game_over_match_ids: set[str],
    game: dict | None,
    now_ns: int,
) -> ExitDecision:
    """Exit logic for underdog reversal positions.

    Holds until: take_profit=0.75, absolute stop=0.04, game_over, max_hold_timeout,
    or Aegis grabbed by the leading team (comeback structurally dead).
    No time horizon — the whole point is to hold through the volatile reprice.
    """
    raw_bid = (book or {}).get("best_bid")
    bid = float(raw_bid) if raw_bid is not None else None
    age_sec = (now_ns - position.entry_time_ns) / 1e9

    # Aegis-as-comeback-killer: if the leading team grabs Roshan/Aegis, exit.
    if game is not None:
        derived = game.get("realtime_derived_events") or []
        try:
            radiant_lead = int(game.get("radiant_lead") or 0)
        except (TypeError, ValueError):
            radiant_lead = 0
        radiant_leads = radiant_lead >= UNDERDOG_REVERSAL_LEAD_THRESHOLD
        dire_leads = radiant_lead <= -UNDERDOG_REVERSAL_LEAD_THRESHOLD
        if radiant_leads and "AEGIS_HELD_BY_RADIANT" in derived:
            return ExitDecision(True, "aegis_comeback_killed", bid)
        if dire_leads and "AEGIS_HELD_BY_DIRE" in derived:
            return ExitDecision(True, "aegis_comeback_killed", bid)

    if position.match_id in game_over_match_ids:
        return ExitDecision(True, "game_over", bid)

    if bid is None:
        if age_sec >= MAX_HOLD_HOURS * 3600:
            return ExitDecision(True, "max_hold_timeout")
        return ExitDecision(False)

    if bid >= UNDERDOG_REVERSAL_TAKE_PROFIT:
        return ExitDecision(True, "take_profit", bid)
    if bid <= UNDERDOG_REVERSAL_STOP_ABS:
        return ExitDecision(True, "stop_loss", bid)
    if age_sec >= MAX_HOLD_HOURS * 3600:
        return ExitDecision(True, "max_hold_timeout", bid)

    return ExitDecision(False)
