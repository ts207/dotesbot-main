from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

import winprob
from actual_dota_event_types import ActualDotaEvent, PRIMITIVE_EVENT_TYPES
from config import (
    EVENT_TRIGGERED_VALUE_ENABLED,
    EVENT_VALUE_MAX_ASK,
    EVENT_VALUE_MAX_EDGE,
    EVENT_VALUE_MIN_ASK,
    EVENT_VALUE_MIN_EDGE,
    EVENT_VALUE_MIN_FAIR_DELTA,
    EVENT_VALUE_TRADE_USD,
    EVENT_VALUE_MIN_GAME_TIME,
    EVENT_VALUE_MAX_GAME_TIME,
    EVENT_VALUE_REVERSAL_MIN_EDGE,
    EVENT_VALUE_REVERSAL_MIN_FAIR_DELTA,
    EVENT_VALUE_REVERSAL_MAX_ASK,
    EVENT_VALUE_REVERSAL_MIN_ASK,
)
from derived_game_state import derive_game_state
from execution_policy import PolicyInput, evaluate_policy, signal_policy_fields
from gettoplive_state import validate_top_live_state
import strategy_registry
from value_engine import VALUE_FLIP_ASK_FLOOR, VALUE_FLIP_LEAD, VALUE_MAX_BOOK_AGE_MS


from fair_value import compute_side_fair

_NAMESPACE = uuid.UUID("11111111-2222-3333-4444-555555555560")

def _make_signal_id(match_id: str, event_id: str, token_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"event_value|{match_id}|{event_id}|{token_id}"))

def _event_type_value(event_type: Any) -> str:
    return str(getattr(event_type, "value", event_type) or "")


def _first_float(mapping: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = mapping.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None

@dataclass(frozen=True)
class EventTriggeredValueSignal:
    signal_id: str
    event_id: str
    actual_event_type: str
    match_id: str
    received_at_ns: int
    direction: str
    side: str
    token_id: str
    fair_before: float
    fair_after: float
    fair_delta: float
    fair_before_raw: float | None
    fair_before_used: float | None
    fair_after_raw: float | None
    fair_after_used: float | None
    fair_delta_raw: float | None
    fair_delta_used: float | None
    model_available: bool
    model_reason: str
    market_price_before_event: float | None
    market_price_after_event: float | None
    market_reprice: float | None
    remaining_event_edge: float | None
    event_reprice_gap: float | None
    edge_type: str
    target_horizon: str
    expected_hold_sec: int
    entry_trigger: str
    exit_trigger: str
    primary_metric: str
    secondary_metric: str
    promotion_rule: str
    disable_rule: str
    bounce_target: float | None
    timeout_sec: int | None
    ask: float
    edge: float
    lead: int
    game_time_sec: int
    elo_diff: float | None
    sized_usd: float
    book_age_ms: int
    derived_state_flags: tuple[str, ...]
    is_reversal: bool
    would_pass_live_gates: bool = True
    would_pass_live: bool = True
    live_skip_reason: str = ""
    paper_only_bypass: bool = False
    policy_allowed: bool | None = None
    policy_reason: str = ""
    policy_version: str = ""
    risk_tags: str = ""

    @property
    def fair_price(self) -> float:
        return self.fair_after_used if self.fair_after_used is not None else self.fair_after

    @property
    def is_continuation(self) -> bool:
        return not self.is_reversal

    def to_signal_dict(self) -> dict:
        strategy_kind = "EVENT_REVERSAL_EDGE" if self.is_reversal else "EVENT_CONTINUATION_EDGE"
        return {
            "signal_id": self.signal_id,
            "match_id": self.match_id,
            "decision": "paper_buy_yes",
            "reason": "event_triggered_value_edge",
            "token_id": self.token_id,
            "side": self.side,
            "fair_after": self.fair_after,
            "fair_price": self.fair_after,
            "fair_before": self.fair_before,
            "fair_delta": self.fair_delta,
            "fair_before_raw": self.fair_before_raw,
            "fair_before_used": self.fair_before_used,
            "fair_after_raw": self.fair_after_raw,
            "fair_after_used": self.fair_after_used,
            "fair_delta_raw": self.fair_delta_raw,
            "fair_delta_used": self.fair_delta_used,
            "model_available": self.model_available,
            "model_reason": self.model_reason,
            "market_price_before_event": self.market_price_before_event,
            "market_price_after_event": self.market_price_after_event,
            "market_reprice": self.market_reprice,
            "remaining_event_edge": self.remaining_event_edge,
            "event_reprice_gap": self.event_reprice_gap,
            "executable_edge": self.edge,
            "edge_type": self.edge_type,
            "target_horizon": self.target_horizon,
            "expected_hold_sec": self.expected_hold_sec,
            "entry_trigger": self.entry_trigger,
            "exit_trigger": self.exit_trigger,
            "primary_metric": self.primary_metric,
            "secondary_metric": self.secondary_metric,
            "promotion_rule": self.promotion_rule,
            "disable_rule": self.disable_rule,
            "bounce_target": self.bounce_target,
            "timeout_sec": self.timeout_sec,
            "expected_move": 0.0,
            "target_size_usd": self.sized_usd,
            "event_type": strategy_kind,
            "strategy_kind": strategy_kind,
            "strategy_family": "EVENT",
            "strategy_subtype": self.actual_event_type,
            "hold_policy": "reversal_bounce_or_thesis" if self.is_reversal else "thesis_invalidation",
            "actual_event_type": self.actual_event_type,
            "event_tier": "A",
            "event_is_primary": True,
            "event_quality": 1.0,
            "event_direction": self.direction,
            "is_reversal": self.is_reversal,
            "is_continuation": self.is_continuation,
            "derived_state_flags": ",".join(self.derived_state_flags),
            "ask": self.ask,
            "edge": self.edge,
            "game_time_sec": self.game_time_sec,
            "lead": self.lead,
            "would_pass_live_gates": self.would_pass_live_gates,
            "would_pass_live": self.would_pass_live,
            "live_skip_reason": self.live_skip_reason,
            "paper_only_bypass": self.paper_only_bypass,
            "policy_allowed": self.policy_allowed,
            "policy_reason": self.policy_reason,
            "policy_version": self.policy_version,
            "risk_tags": self.risk_tags,
            "max_fill_price": EVENT_VALUE_REVERSAL_MAX_ASK if self.is_reversal else EVENT_VALUE_MAX_ASK,
        }


@dataclass(frozen=True)
class EventTriggeredValueReject:
    match_id: str
    received_at_ns: int
    reason: str
    event_id: str = ""
    actual_event_type: str = ""
    direction: str = ""
    side: str = ""
    token_id: str = ""
    fair_before: float | None = None
    fair_after: float | None = None
    fair_delta: float | None = None
    fair_before_raw: float | None = None
    fair_before_used: float | None = None
    fair_after_raw: float | None = None
    fair_after_used: float | None = None
    fair_delta_raw: float | None = None
    fair_delta_used: float | None = None
    model_available: bool | None = None
    model_reason: str | None = None
    market_price_before_event: float | None = None
    market_price_after_event: float | None = None
    market_reprice: float | None = None
    remaining_event_edge: float | None = None
    event_reprice_gap: float | None = None
    edge_type: str = ""
    target_horizon: str = ""
    expected_hold_sec: int | None = None
    entry_trigger: str = ""
    exit_trigger: str = ""
    primary_metric: str = ""
    secondary_metric: str = ""
    promotion_rule: str = ""
    disable_rule: str = ""
    bounce_target: float | None = None
    timeout_sec: int | None = None
    ask: float | None = None
    edge: float | None = None
    lead: int | None = None
    game_time_sec: int | None = None
    elo_diff: float | None = None
    book_age_ms: int | None = None
    is_reversal: bool | None = None
    would_pass_live_gates: bool = False
    live_skip_reason: str = ""
    paper_only_bypass: bool = False

    @property
    def fair_price(self) -> float | None:
        return self.fair_after


def _strategy_contract(is_reversal: bool) -> dict[str, Any]:
    kind = "EVENT_REVERSAL_EDGE" if is_reversal else "EVENT_CONTINUATION_EDGE"
    return strategy_registry.get(kind).signal_kwargs(include_bounce_fields=True)


class EventTriggeredValueEngine:
    def evaluate(
        self,
        *,
        event: ActualDotaEvent,
        game: Mapping[str, Any],
        mapping: Mapping[str, Any],
        book_store: Any,
        entered_tokens: Any = None,
        pre_event_books: dict[str, dict] | None = None,
    ) -> list[EventTriggeredValueSignal | EventTriggeredValueReject]:
        if not EVENT_TRIGGERED_VALUE_ENABLED:
            return []
        match_id = str(game.get("match_id") or "")
        cur_ns = int(game.get("received_at_ns") or event.received_at_ns or 0)
        if not match_id:
            return []
        if game.get("data_source") != "top_live" or event.source != "top_live":
            return [self._reject(event, match_id, cur_ns, "not_top_live")]
        actual_event_type = _event_type_value(event.event_type)
        if actual_event_type not in PRIMITIVE_EVENT_TYPES:
            return [self._reject(event, match_id, cur_ns, "unsupported_actual_event_type")]
        if (
            actual_event_type == "MULTI_KILL_WINDOW"
            and not bool(getattr(event, "live_grade_event", True))
        ):
            return [self._reject(event, match_id, cur_ns, "multi_kill_not_live_grade")]
        if actual_event_type == "GAME_ENDED" or game.get("game_over"):
            return [self._reject(event, match_id, cur_ns, "game_over")]
        if event.side not in {"radiant", "dire"}:
            return [self._reject(event, match_id, cur_ns, "event_side_not_tradeable")]

        state_check = validate_top_live_state(game)
        if not state_check.ok:
            missing = ",".join(state_check.missing_fields)
            reason = state_check.reason if not missing else f"{state_check.reason}:{missing}"
            return [self._reject(event, match_id, cur_ns, reason)]

        game_time = int(game.get("game_time_sec") or 0)
        if game_time < EVENT_VALUE_MIN_GAME_TIME:
            return [self._reject(event, match_id, cur_ns, "game_too_early", game_time_sec=game_time)]
        if game_time > EVENT_VALUE_MAX_GAME_TIME:
            return [self._reject(event, match_id, cur_ns, "game_too_late", game_time_sec=game_time)]
            
        lead_after = event.radiant_lead_after
        lead_before = event.radiant_lead_before
        if lead_after is None or lead_before is None:
            return [self._reject(event, match_id, cur_ns, "missing_event_lead")]

        direction = event.side
        market_type = str(mapping.get("market_type") or "").upper()
        if market_type == "MATCH_WINNER":
            try:
                from market_scope import is_game3_match_proxy
                is_g3 = is_game3_match_proxy(mapping)
            except Exception:
                is_g3 = False
            if not is_g3:
                return [self._reject(event, match_id, cur_ns, "series_market_unpriced")]
        elif market_type != "MAP_WINNER":
            return [self._reject(event, match_id, cur_ns, "unsupported_market_type", direction=direction)]

        side_map = mapping.get("steam_side_mapping", "normal")
        if side_map == "normal":
            side = "YES" if direction == "radiant" else "NO"
        elif side_map == "reversed":
            side = "NO" if direction == "radiant" else "YES"
        else:
            return [self._reject(event, match_id, cur_ns, "unknown_side_mapping", direction=direction)]
        token_id = mapping.get("yes_token_id") if side == "YES" else mapping.get("no_token_id")
        if not token_id:
            return [self._reject(event, match_id, cur_ns, "missing_token_id", direction=direction, side=side)]

        entered = {str(t) for t in (entered_tokens or [])}
        if str(token_id) in entered:
            return [self._reject(event, match_id, cur_ns, "token_already_entered", direction=direction, side=side, token_id=token_id)]

        book = book_store.get(token_id) if book_store else None
        if not book:
            return [self._reject(event, match_id, cur_ns, "missing_book", direction=direction, side=side, token_id=token_id)]
        try:
            ask = float(book.get("best_ask"))
        except (TypeError, ValueError):
            return [self._reject(event, match_id, cur_ns, "missing_ask", direction=direction, side=side, token_id=token_id)]
        received_at_ns = book.get("received_at_ns")
        if not received_at_ns:
            return [self._reject(event, match_id, cur_ns, "book_no_timestamp", direction=direction, side=side, token_id=token_id, ask=ask)]
        book_age_ms = int((time.time_ns() - received_at_ns) / 1_000_000)
        if book_age_ms > VALUE_MAX_BOOK_AGE_MS:
            return [self._reject(event, match_id, cur_ns, "book_stale", direction=direction, side=side, token_id=token_id, ask=ask, book_age_ms=book_age_ms)]

        current_leader_side = "radiant" if lead_after > 0 else "dire"
        is_reversal = (direction != current_leader_side)
        
        min_ask = EVENT_VALUE_REVERSAL_MIN_ASK if is_reversal else EVENT_VALUE_MIN_ASK
        max_ask = EVENT_VALUE_REVERSAL_MAX_ASK if is_reversal else EVENT_VALUE_MAX_ASK

        if ask > max_ask:
            return [self._reject(event, match_id, cur_ns, "price_too_high", direction=direction, side=side, token_id=token_id, ask=ask, book_age_ms=book_age_ms, is_reversal=is_reversal)]
        if ask < min_ask:
            return [self._reject(event, match_id, cur_ns, "price_too_low", direction=direction, side=side, token_id=token_id, ask=ask, book_age_ms=book_age_ms, is_reversal=is_reversal)]

        side_lead_after = lead_after if direction == "radiant" else -lead_after
        if abs(side_lead_after) > VALUE_FLIP_LEAD and ask < VALUE_FLIP_ASK_FLOOR:
            return [self._reject(event, match_id, cur_ns, "orientation_flip_suspected", direction=direction, side=side, token_id=token_id, ask=ask, lead=lead_after, book_age_ms=book_age_ms)]

        res_before = compute_side_fair(game=game, side=direction, radiant_lead_override=lead_before, received_at_ns_override=cur_ns, record_history=False)
        res_after = compute_side_fair(game=game, side=direction, radiant_lead_override=lead_after, received_at_ns_override=cur_ns, record_history=False)
        
        model_available = bool(res_before.model_available and res_after.model_available)
        model_reason = "ok" if model_available else f"{res_before.model_reason}|{res_after.model_reason}"
        if not model_available:
            return [self._reject(
                event, match_id, cur_ns, "model_unavailable",
                direction=direction, side=side, token_id=token_id,
                fair_before_raw=res_before.fair_raw,
                fair_before_used=res_before.fair_used,
                fair_after_raw=res_after.fair_raw,
                fair_after_used=res_after.fair_used,
                model_available=model_available,
                model_reason=model_reason,
                ask=ask, lead=lead_after, game_time_sec=game_time,
                book_age_ms=book_age_ms, is_reversal=is_reversal,
                **_strategy_contract(is_reversal),
            )]

        fair_before = res_before.fair_used if res_before.fair_used is not None else res_before.fair
        fair_after = res_after.fair_used if res_after.fair_used is not None else res_after.fair
        elo_diff = res_after.elo_diff
        
        fair_delta = fair_after - fair_before
        fair_delta_raw = (
            None if res_before.fair_raw is None or res_after.fair_raw is None
            else res_after.fair_raw - res_before.fair_raw
        )
        pre_event_book = pre_event_books.get(token_id) if pre_event_books else None
        if pre_event_book:
            market_price_before_event = _first_float(
                pre_event_book,
                "ask",
                "mid",
            )
        else:
            market_price_before_event = _first_float(
                book,
                "market_price_before_event",
                "best_ask_before_event",
                "pre_event_ask",
                "pre_event_price",
            )
        market_price_after_event = ask
        market_reprice = (
            None if market_price_before_event is None
            else market_price_after_event - market_price_before_event
        )
        remaining_event_edge = fair_after - ask
        event_reprice_gap = (
            None if market_reprice is None
            else fair_delta - market_reprice
        )
        edge = remaining_event_edge
        contract = _strategy_contract(is_reversal)
        
        min_fair_delta = EVENT_VALUE_REVERSAL_MIN_FAIR_DELTA if is_reversal else EVENT_VALUE_MIN_FAIR_DELTA
        min_edge = EVENT_VALUE_REVERSAL_MIN_EDGE if is_reversal else EVENT_VALUE_MIN_EDGE
        
        if fair_delta < min_fair_delta:
            return [self._reject(event, match_id, cur_ns, "fair_delta_too_small", direction=direction, side=side, token_id=token_id, fair_before=fair_before, fair_after=fair_after, fair_delta=fair_delta, fair_before_raw=res_before.fair_raw, fair_before_used=fair_before, fair_after_raw=res_after.fair_raw, fair_after_used=fair_after, fair_delta_raw=fair_delta_raw, fair_delta_used=fair_delta, model_available=model_available, model_reason=model_reason, market_price_before_event=market_price_before_event, market_price_after_event=market_price_after_event, market_reprice=market_reprice, remaining_event_edge=remaining_event_edge, event_reprice_gap=event_reprice_gap, ask=ask, edge=edge, lead=lead_after, game_time_sec=game_time, elo_diff=elo_diff, book_age_ms=book_age_ms, is_reversal=is_reversal, **contract)]
        if event_reprice_gap is not None and event_reprice_gap < min_edge:
            return [self._reject(event, match_id, cur_ns, "event_reprice_gap_too_small", direction=direction, side=side, token_id=token_id, fair_before=fair_before, fair_after=fair_after, fair_delta=fair_delta, fair_before_raw=res_before.fair_raw, fair_before_used=fair_before, fair_after_raw=res_after.fair_raw, fair_after_used=fair_after, fair_delta_raw=fair_delta_raw, fair_delta_used=fair_delta, model_available=model_available, model_reason=model_reason, market_price_before_event=market_price_before_event, market_price_after_event=market_price_after_event, market_reprice=market_reprice, remaining_event_edge=remaining_event_edge, event_reprice_gap=event_reprice_gap, ask=ask, edge=edge, lead=lead_after, game_time_sec=game_time, elo_diff=elo_diff, book_age_ms=book_age_ms, is_reversal=is_reversal, **contract)]
        if edge < min_edge:
            return [self._reject(event, match_id, cur_ns, "edge_too_small", direction=direction, side=side, token_id=token_id, fair_before=fair_before, fair_after=fair_after, fair_delta=fair_delta, fair_before_raw=res_before.fair_raw, fair_before_used=fair_before, fair_after_raw=res_after.fair_raw, fair_after_used=fair_after, fair_delta_raw=fair_delta_raw, fair_delta_used=fair_delta, model_available=model_available, model_reason=model_reason, market_price_before_event=market_price_before_event, market_price_after_event=market_price_after_event, market_reprice=market_reprice, remaining_event_edge=remaining_event_edge, event_reprice_gap=event_reprice_gap, ask=ask, edge=edge, lead=lead_after, game_time_sec=game_time, elo_diff=elo_diff, book_age_ms=book_age_ms, is_reversal=is_reversal, **contract)]
        if edge > EVENT_VALUE_MAX_EDGE:
            return [self._reject(event, match_id, cur_ns, "edge_too_large", direction=direction, side=side, token_id=token_id, fair_before=fair_before, fair_after=fair_after, fair_delta=fair_delta, fair_before_raw=res_before.fair_raw, fair_before_used=fair_before, fair_after_raw=res_after.fair_raw, fair_after_used=fair_after, fair_delta_raw=fair_delta_raw, fair_delta_used=fair_delta, model_available=model_available, model_reason=model_reason, market_price_before_event=market_price_before_event, market_price_after_event=market_price_after_event, market_reprice=market_reprice, remaining_event_edge=remaining_event_edge, event_reprice_gap=event_reprice_gap, ask=ask, edge=edge, lead=lead_after, game_time_sec=game_time, elo_diff=elo_diff, book_age_ms=book_age_ms, is_reversal=is_reversal, **contract)]

        derived = derive_game_state(game)
        policy_fields = signal_policy_fields(evaluate_policy(PolicyInput(
            mode="paper_research",
            strategy_kind="EVENT_REVERSAL_EDGE" if is_reversal else "EVENT_CONTINUATION_EDGE",
            market_type=str(mapping.get("market_type") or ""),
            token_id=str(token_id),
            side=side,
            signal={
                "event_type": "EVENT_TRIGGERED_VALUE",
                "strategy_kind": "EVENT_REVERSAL_EDGE" if is_reversal else "EVENT_CONTINUATION_EDGE",
                "token_id": str(token_id),
                "side": side,
                "fair_price": fair_after,
                "executable_edge": edge,
                "ask": ask,
                "max_fill_price": max_ask,
                "target_horizon": contract.get("target_horizon"),
                "expected_hold_sec": contract.get("expected_hold_sec"),
            },
            game=dict(game),
            mapping=dict(mapping),
            book=dict(book),
            now_ns=time.time_ns(),
        )))
        return [EventTriggeredValueSignal(
            signal_id=_make_signal_id(match_id, event.event_id, str(token_id)),
            event_id=event.event_id,
            actual_event_type=actual_event_type,
            match_id=match_id,
            received_at_ns=cur_ns,
            direction=direction,
            side=side,
            token_id=str(token_id),
            fair_before=fair_before,
            fair_after=fair_after,
            fair_delta=fair_delta,
            fair_before_raw=res_before.fair_raw,
            fair_before_used=fair_before,
            fair_after_raw=res_after.fair_raw,
            fair_after_used=fair_after,
            fair_delta_raw=fair_delta_raw,
            fair_delta_used=fair_delta,
            model_available=model_available,
            model_reason=model_reason,
            market_price_before_event=market_price_before_event,
            market_price_after_event=market_price_after_event,
            market_reprice=market_reprice,
            remaining_event_edge=remaining_event_edge,
            event_reprice_gap=event_reprice_gap,
            **contract,
            ask=ask,
            edge=edge,
            lead=lead_after,
            game_time_sec=game_time,
            elo_diff=elo_diff,
            sized_usd=EVENT_VALUE_TRADE_USD * 2.0 if is_reversal else EVENT_VALUE_TRADE_USD,
            book_age_ms=book_age_ms,
            derived_state_flags=derived.flags,
            is_reversal=is_reversal,
            **policy_fields,
        )]

    def _reject(self, event: ActualDotaEvent, match_id: str, received_at_ns: int, reason: str, **kwargs) -> EventTriggeredValueReject:
        return EventTriggeredValueReject(
            match_id=match_id,
            received_at_ns=received_at_ns,
            reason=reason,
            event_id=event.event_id,
            actual_event_type=_event_type_value(event.event_type),
            **kwargs,
        )
