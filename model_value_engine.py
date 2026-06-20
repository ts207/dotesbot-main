from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Mapping, Any

import model_value_predictor
import strategy_registry
from config import (
    MODEL_VALUE_ENABLED,
    MODEL_VALUE_MIN_EDGE,
    MODEL_VALUE_CONFIRM_ENABLED,
    MODEL_VALUE_CONFIRM_MIN_EDGE,
    MODEL_VALUE_CONFIRM_MAX_AGE_SEC,
    MODEL_VALUE_CONFIRM_MAX_ASK_WORSEN,
    MODEL_VALUE_MIN_ASK,
    MODEL_VALUE_MAX_ASK,
    MODEL_VALUE_MAX_SPREAD,
    MODEL_VALUE_MAX_BOOK_AGE_MS,
    MODEL_VALUE_TRADE_USD,
    MODEL_VALUE_MODEL_PATH,
)
from execution_policy import PolicyInput, evaluate_policy, signal_policy_fields
from gettoplive_state import validate_top_live_state

_NAMESPACE = uuid.UUID("11111111-2222-3333-4444-555555555557")

def _make_signal_id(match_id: str, received_at_ns: int, token_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"model_value|{match_id}|{token_id}|{received_at_ns}"))

@dataclass(frozen=True)
class ModelValueSignal:
    signal_id: str
    match_id: str
    received_at_ns: int
    direction: str
    side: str
    token_id: str
    fair_price: float
    ask: float
    edge: float
    game_time_sec: int
    book_age_ms: int
    model_version: str
    model_reason: str
    sized_usd: float

    token_net_worth_lead: float
    token_score_margin: float
    radiant_net_worth: float
    dire_net_worth: float
    radiant_score: float
    dire_score: float

    would_pass_live_gates: bool = True
    would_pass_live: bool = True
    live_skip_reason: str = ""
    paper_only_bypass: bool = False
    policy_allowed: bool | None = None
    policy_reason: str = ""
    policy_version: str = ""
    risk_tags: str = ""

    edge_type: str = field(default_factory=lambda: strategy_registry.get("MODEL_VALUE_EDGE").edge_type)
    target_horizon: str = field(default_factory=lambda: strategy_registry.get("MODEL_VALUE_EDGE").target_horizon)
    expected_hold_sec: int = field(default_factory=lambda: strategy_registry.get("MODEL_VALUE_EDGE").expected_hold_sec)
    entry_trigger: str = field(default_factory=lambda: strategy_registry.get("MODEL_VALUE_EDGE").entry_trigger)
    exit_trigger: str = field(default_factory=lambda: strategy_registry.get("MODEL_VALUE_EDGE").exit_trigger)
    primary_metric: str = field(default_factory=lambda: strategy_registry.get("MODEL_VALUE_EDGE").primary_metric)
    secondary_metric: str = field(default_factory=lambda: strategy_registry.get("MODEL_VALUE_EDGE").secondary_metric)
    promotion_rule: str = field(default_factory=lambda: strategy_registry.get("MODEL_VALUE_EDGE").promotion_rule)
    disable_rule: str = field(default_factory=lambda: strategy_registry.get("MODEL_VALUE_EDGE").disable_rule)

    def to_signal_dict(self) -> dict:
        return {
            "signal_id": self.signal_id,
            "match_id": self.match_id,
            "decision": "paper_buy_yes",
            "reason": "model_value_edge",
            "token_id": self.token_id,
            "side": self.side,
            "fair_price": self.fair_price,
            "executable_edge": self.edge,
            "ask": self.ask,
            "max_fill_price": MODEL_VALUE_MAX_ASK,
            "target_size_usd": self.sized_usd,
            "event_type": "MODEL_VALUE_EDGE",
            "strategy_kind": "MODEL_VALUE_EDGE",
            "strategy_family": "MODEL_VALUE",
            "target_horizon": self.target_horizon,
            "expected_hold_sec": self.expected_hold_sec,
            "event_direction": self.direction,
            "model_version": getattr(self, "model_version", None),
            "model_reason": getattr(self, "model_reason", None),
            "token_net_worth_lead": getattr(self, "token_net_worth_lead", None),
            "token_score_margin": getattr(self, "token_score_margin", None),
            "radiant_net_worth": getattr(self, "radiant_net_worth", None),
            "dire_net_worth": getattr(self, "dire_net_worth", None),
            "radiant_score": getattr(self, "radiant_score", None),
            "dire_score": getattr(self, "dire_score", None),
            "radiant_lead": (getattr(self, "radiant_net_worth", 0) or 0) - (getattr(self, "dire_net_worth", 0) or 0),
            "would_pass_live_gates": self.would_pass_live_gates,
            "would_pass_live": self.would_pass_live,
            "live_skip_reason": self.live_skip_reason,
            "paper_only_bypass": self.paper_only_bypass,
            "policy_allowed": self.policy_allowed,
            "policy_reason": self.policy_reason,
            "policy_version": self.policy_version,
            "risk_tags": self.risk_tags,
            "edge_type": self.edge_type,
            "entry_trigger": self.entry_trigger,
            "exit_trigger": self.exit_trigger,
            "primary_metric": self.primary_metric,
            "secondary_metric": self.secondary_metric,
            "promotion_rule": self.promotion_rule,
            "disable_rule": self.disable_rule,
        }

@dataclass(frozen=True)
class ModelValueReject:
    match_id: str
    received_at_ns: int
    reason: str
    direction: str = ""
    side: str = ""
    token_id: str = ""
    fair_price: float | None = None
    ask: float | None = None
    edge: float | None = None
    game_time_sec: int | None = None
    book_age_ms: int | None = None

_MODEL_VALUE_CONFIRM_STATE: dict[str, dict] = {}

def _model_value_confirmation_passes(result: ModelValueSignal) -> tuple[bool, str]:
    """Require persistent MODEL_VALUE edge before entering real/paper trading."""
    if not MODEL_VALUE_CONFIRM_ENABLED:
        return True, "disabled"

    min_edge = MODEL_VALUE_CONFIRM_MIN_EDGE
    max_age_sec = MODEL_VALUE_CONFIRM_MAX_AGE_SEC
    max_ask_worsen = MODEL_VALUE_CONFIRM_MAX_ASK_WORSEN
    key = f"{result.match_id}|{result.token_id}|{result.side}"
    now_ns = int(result.received_at_ns or time.time_ns())

    prior = _MODEL_VALUE_CONFIRM_STATE.get(key)
    if result.edge < min_edge:
        _MODEL_VALUE_CONFIRM_STATE.pop(key, None)
        return False, f"model_value_confirm_edge_too_low:edge={result.edge:.4f}_min={min_edge:.4f}"

    if not prior:
        _MODEL_VALUE_CONFIRM_STATE[key] = {
            "received_at_ns": now_ns,
            "ask": result.ask,
            "edge": result.edge,
            "signal_id": result.signal_id,
        }
        return False, "model_value_confirm_armed"

    age_sec = max(0.0, (now_ns - int(prior["received_at_ns"])) / 1e9)
    ask_worsen = float(result.ask) - float(prior["ask"])
    if age_sec > max_age_sec:
        _MODEL_VALUE_CONFIRM_STATE[key] = {
            "received_at_ns": now_ns,
            "ask": result.ask,
            "edge": result.edge,
            "signal_id": result.signal_id,
        }
        return False, f"model_value_confirm_expired:age={age_sec:.1f}_max={max_age_sec:.1f}"
    if ask_worsen > max_ask_worsen:
        _MODEL_VALUE_CONFIRM_STATE[key] = {
            "received_at_ns": now_ns,
            "ask": result.ask,
            "edge": result.edge,
            "signal_id": result.signal_id,
        }
        return False, f"model_value_confirm_ask_worsened:delta={ask_worsen:.4f}_max={max_ask_worsen:.4f}"

    _MODEL_VALUE_CONFIRM_STATE.pop(key, None)
    return True, f"model_value_confirmed:age={age_sec:.1f}_ask_delta={ask_worsen:.4f}"

class ModelValueEngine:
    def __init__(self) -> None:
        # Eagerly load the model
        model_value_predictor.load_model(MODEL_VALUE_MODEL_PATH)

    def evaluate(self, game: dict, mapping: dict, book_store: Any, entered_tokens: set[str], mode: str = "paper_research") -> list[ModelValueSignal | ModelValueReject]:
        if not MODEL_VALUE_ENABLED:
            return []

        match_id = str(game.get("match_id") or "")
        if not match_id:
            return []

        # Only process top_live updates (skip slow league stream updates)
        if game.get("data_source") != "top_live":
            return []

        cur_ns = int(game.get("received_at_ns") or time.time_ns())

        # Ensure model is loaded (robust recovery check)
        if model_value_predictor._MODEL_DATA is None:
            loaded = model_value_predictor.load_model(MODEL_VALUE_MODEL_PATH)
            if not loaded:
                return [ModelValueReject(match_id, cur_ns, "model_load_failed")]

        state_check = validate_top_live_state(game)
        if not state_check.ok:
            missing = ",".join(state_check.missing_fields)
            reason = state_check.reason if not missing else f"{state_check.reason}:{missing}"
            return [ModelValueReject(match_id, cur_ns, reason)]

        if game.get("game_over"):
            return [ModelValueReject(match_id, cur_ns, "game_over")]

        game_time = game.get("game_time_sec")
        if game_time is None:
            return [ModelValueReject(match_id, cur_ns, "missing_game_time")]

        # Validate market type
        market_type = str(mapping.get("market_type") or "").upper()
        if market_type == "MATCH_WINNER":
            try:
                from market_scope import is_game3_match_proxy
                _is_g3 = is_game3_match_proxy(mapping)
            except Exception:
                _is_g3 = False
            if not _is_g3:
                return [ModelValueReject(match_id, cur_ns, "series_market_unpriced", game_time_sec=game_time)]
        elif market_type != "MAP_WINNER":
            return [ModelValueReject(match_id, cur_ns, "unsupported_market_type", game_time_sec=game_time)]

        candidates: list[ModelValueSignal] = []
        rejects: list[ModelValueReject] = []

        # Evaluate both sides (Radiant and Dire)
        for direction in ["radiant", "dire"]:
            side_map = mapping.get("steam_side_mapping", "normal")
            if side_map == "normal":
                market_side = "YES" if direction == "radiant" else "NO"
            elif side_map == "reversed":
                market_side = "NO" if direction == "radiant" else "YES"
            else:
                rejects.append(ModelValueReject(match_id, cur_ns, "unknown_side_mapping", direction=direction, game_time_sec=game_time))
                continue

            token_id = mapping.get("yes_token_id") if market_side == "YES" else mapping.get("no_token_id")
            opposing_token_id = mapping.get("no_token_id") if market_side == "YES" else mapping.get("yes_token_id")
            if not token_id or not opposing_token_id:
                rejects.append(ModelValueReject(match_id, cur_ns, "missing_token_id", direction=direction, side=market_side, game_time_sec=game_time))
                continue

            book = book_store.get(token_id) if book_store else None
            paired_book = book_store.get(opposing_token_id) if book_store else None
            if not book:
                rejects.append(ModelValueReject(match_id, cur_ns, "missing_book", direction=direction, side=market_side, token_id=token_id, game_time_sec=game_time))
                continue

            # Build side features
            features = model_value_predictor.build_side_features(game, mapping, direction, book, paired_book)
            pred = model_value_predictor.predict_probability(features)

            if not pred.get("features_available", False):
                rejects.append(ModelValueReject(
                    match_id, cur_ns, f"features_unavailable:{pred.get('reason')}",
                    direction=direction, side=market_side, token_id=token_id, game_time_sec=game_time
                ))
                continue

            p = pred["model_probability"]

            try:
                ask = float(book.get("best_ask"))
                bid = float(book.get("best_bid", 0.0))
            except (TypeError, ValueError):
                rejects.append(ModelValueReject(
                    match_id, cur_ns, "missing_ask",
                    direction=direction, side=market_side, token_id=token_id, game_time_sec=game_time
                ))
                continue

            received_at_ns = book.get("received_at_ns")
            if not received_at_ns:
                rejects.append(ModelValueReject(
                    match_id, cur_ns, "book_no_timestamp",
                    direction=direction, side=market_side, token_id=token_id, ask=ask, game_time_sec=game_time
                ))
                continue

            book_age_ms = int((time.time_ns() - received_at_ns) / 1_000_000)
            spread = ask - bid
            edge = p - ask

            # Engine filters
            if edge < MODEL_VALUE_MIN_EDGE:
                rejects.append(ModelValueReject(
                    match_id, cur_ns, "edge_too_small",
                    direction=direction, side=market_side, token_id=token_id,
                    fair_price=p, ask=ask, edge=edge, game_time_sec=game_time, book_age_ms=book_age_ms
                ))
                continue

            if spread > MODEL_VALUE_MAX_SPREAD:
                rejects.append(ModelValueReject(
                    match_id, cur_ns, "spread_too_large",
                    direction=direction, side=market_side, token_id=token_id,
                    fair_price=p, ask=ask, edge=edge, game_time_sec=game_time, book_age_ms=book_age_ms
                ))
                continue

            if book_age_ms > MODEL_VALUE_MAX_BOOK_AGE_MS:
                rejects.append(ModelValueReject(
                    match_id, cur_ns, "book_stale",
                    direction=direction, side=market_side, token_id=token_id,
                    fair_price=p, ask=ask, edge=edge, game_time_sec=game_time, book_age_ms=book_age_ms
                ))
                continue

            if not (MODEL_VALUE_MIN_ASK <= ask <= MODEL_VALUE_MAX_ASK):
                rejects.append(ModelValueReject(
                    match_id, cur_ns, "ask_out_of_bounds",
                    direction=direction, side=market_side, token_id=token_id,
                    fair_price=p, ask=ask, edge=edge, game_time_sec=game_time, book_age_ms=book_age_ms
                ))
                continue

            # Run policy evaluation through evaluate_policy
            policy_fields = signal_policy_fields(evaluate_policy(PolicyInput(
                mode=mode,
                strategy_kind="MODEL_VALUE_EDGE",
                market_type=market_type,
                token_id=str(token_id),
                side=market_side,
                signal={
                    "event_type": "MODEL_VALUE_EDGE",
                    "strategy_kind": "MODEL_VALUE_EDGE",
                    "token_id": str(token_id),
                    "side": market_side,
                    "fair_price": p,
                    "executable_edge": edge,
                    "ask": ask,
                    "max_fill_price": MODEL_VALUE_MAX_ASK,
                    "target_horizon": "settlement",
                    "expected_hold_sec": 0,
                },
                game=dict(game),
                mapping=dict(mapping),
                book=dict(book),
                now_ns=time.time_ns(),
            )))

            signal = ModelValueSignal(
                signal_id=_make_signal_id(match_id, cur_ns, token_id),
                match_id=match_id,
                received_at_ns=cur_ns,
                direction=direction,
                side=market_side,
                token_id=token_id,
                fair_price=p,
                ask=ask,
                edge=edge,
                game_time_sec=game_time,
                book_age_ms=book_age_ms,
                model_version=pred["model_version"],
                model_reason=pred["reason"],
                sized_usd=MODEL_VALUE_TRADE_USD,
                token_net_worth_lead=features.get("token_net_worth_lead", 0.0),
                token_score_margin=features.get("token_score_margin", 0.0),
                radiant_net_worth=features.get("radiant_net_worth", 0.0),
                dire_net_worth=features.get("dire_net_worth", 0.0),
                radiant_score=features.get("radiant_score", 0.0),
                dire_score=features.get("dire_score", 0.0),
                **policy_fields,
            )
            candidates.append(signal)

        if candidates:
            # Choose the side with the highest edge
            candidates.sort(key=lambda x: x.edge, reverse=True)
            return [candidates[0]]

        return rejects
