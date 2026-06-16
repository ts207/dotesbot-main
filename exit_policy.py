from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import config
from config import (
    CATASTROPHE_FLOOR,
    CATASTROPHE_NW_CONFIRM,
    MAX_HOLD_HOURS,
    VALUE_EXIT_FAIR_BID_BUFFER,
    VALUE_EXIT_FAIR_ENTRY_BUFFER,
    VALUE_EXIT_FAIR_INVALIDATION_ENABLED,
)


@dataclass(frozen=True)
class ExitDecision:
    should_exit: bool
    reason: str = ""
    reference_bid: float | None = None
    price_floor: float | None = None


class ExitPolicy:
    @staticmethod
    def applies(position: Any) -> bool:
        trader_kind = str(getattr(position, "trader_kind", "") or "").lower()
        strategy_kind = str(getattr(position, "strategy_kind", "") or "").upper()
        hold_policy = str(getattr(position, "hold_policy", "") or "")
        event_type = str(getattr(position, "event_type", "") or "").upper()
        return (
            trader_kind in {"value", "dswing"}
            or strategy_kind in {"VALUE", "VALUE_EDGE", "EVENT_TRIGGERED_VALUE", "EVENT_CONTINUATION_EDGE", "EVENT_REVERSAL_EDGE", "DSWING"}
            or event_type in {"VALUE", "VALUE_EDGE", "VALUE_HOLD", "EVENT_TRIGGERED_VALUE", "EVENT_CONTINUATION_EDGE", "EVENT_REVERSAL_EDGE", "DSWING"}
            or hold_policy in {"thesis_invalidation", "map_end_convergence", "reversal_bounce_or_thesis"}
        )

    @staticmethod
    def decide(
        position: Any,
        book: dict | None,
        game: dict | None,
        game_over_match_ids: set[str],
        *,
        now_ns: int | None = None,
        current_fair: float | None = None,
        catastrophe_floor: float | None = None,
        catastrophe_nw_confirm: float | None = None,
    ) -> ExitDecision:
        now_ns = now_ns or time.time_ns()
        raw_bid = (book or {}).get("best_bid")
        bid = float(raw_bid) if raw_bid is not None else None
        age_sec = (now_ns - position.entry_time_ns) / 1e9
        max_hold_sec = MAX_HOLD_HOURS * 3600

        strategy_kind = str(getattr(position, "strategy_kind", "") or "").upper()
        event_type = str(getattr(position, "event_type", "") or "").upper()
        hold_policy = str(getattr(position, "hold_policy", "") or "")
        trader_kind = str(getattr(position, "trader_kind", "") or "").lower()

        if trader_kind == "dswing" or strategy_kind == "DSWING" or event_type == "DSWING" or hold_policy == "map_end_convergence":
            if position.match_id in game_over_match_ids:
                return ExitDecision(True, "map_end_convergence", bid)
            if age_sec >= max_hold_sec:
                return ExitDecision(True, "max_hold_timeout", bid)
            return ExitDecision(False)

        is_event_reversal = (
            hold_policy == "reversal_bounce_or_thesis"
            or strategy_kind == "EVENT_REVERSAL_EDGE"
            or event_type == "EVENT_REVERSAL_EDGE"
            or getattr(position, "entry_is_reversal", False) is True
        )
        if is_event_reversal:
            if position.match_id in game_over_match_ids:
                return ExitDecision(True, "game_over", bid)
            if age_sec >= max_hold_sec:
                return ExitDecision(True, "max_hold_timeout", bid)
            if not config.EVENT_REVERSAL_ACTIVE_EXITS_ENABLED:
                return ExitDecision(False)
            if bid is None:
                return ExitDecision(False)
            if bid >= position.entry_price + config.EVENT_REVERSAL_TAKE_PROFIT_CENTS:
                return ExitDecision(True, "event_reversal_bounce_take_profit", bid)
            if age_sec >= config.EVENT_REVERSAL_MAX_HOLD_SEC:
                return ExitDecision(True, "event_reversal_timeout", bid)
            if _fair_invalidates(position, bid, current_fair):
                return ExitDecision(True, "event_reversal_fair_invalidation", bid, current_fair)
            return ExitDecision(False)

        is_value = (
            trader_kind == "value"
            or hold_policy == "thesis_invalidation"
            or strategy_kind in {"VALUE", "VALUE_EDGE", "EVENT_TRIGGERED_VALUE", "EVENT_CONTINUATION_EDGE"}
            or event_type in {"VALUE", "VALUE_EDGE", "VALUE_HOLD", "EVENT_TRIGGERED_VALUE", "EVENT_CONTINUATION_EDGE"}
        )
        if is_value:
            if position.match_id in game_over_match_ids:
                return ExitDecision(True, "game_over", bid)
            floor = catastrophe_floor
            if floor is None:
                floor = CATASTROPHE_FLOOR
            nw_confirm = catastrophe_nw_confirm
            if nw_confirm is None:
                nw_confirm = CATASTROPHE_NW_CONFIRM
            if bid is not None and 0.0 < floor and bid < floor:
                backed_direction = getattr(position, "backed_direction", None) or getattr(position, "entry_backed_side", None)
                radiant_lead = _radiant_lead(game)
                if backed_direction in {"radiant", "dire"} and radiant_lead is not None:
                    backed_lead = radiant_lead if backed_direction == "radiant" else -radiant_lead
                    if backed_lead < -nw_confirm:
                        return ExitDecision(True, "catastrophe_salvage", bid)
                elif game is None:
                    return ExitDecision(True, "catastrophe_salvage", bid)
            if bid is not None and _fair_invalidates(position, bid, current_fair):
                return ExitDecision(True, "fair_invalidation", bid, current_fair)
            if age_sec >= max_hold_sec:
                return ExitDecision(True, "max_hold_timeout", bid)
            return ExitDecision(False)

        return ExitDecision(False)


def _fair_invalidates(position: Any, bid: float, current_fair: float | None) -> bool:
    return (
        VALUE_EXIT_FAIR_INVALIDATION_ENABLED
        and current_fair is not None
        and current_fair < position.entry_price - VALUE_EXIT_FAIR_ENTRY_BUFFER
        and current_fair < bid - VALUE_EXIT_FAIR_BID_BUFFER
    )


def _radiant_lead(game: dict | None) -> int | None:
    if game is None:
        return None
    try:
        return int(float(game.get("radiant_lead")))
    except (TypeError, ValueError):
        return None
