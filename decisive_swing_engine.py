"""Decisive-swing ML sniper (2026-06-03).

Edge (validated: +8c convergence, 82% positive on n=34): when a Dota game's
net-worth lead crosses a GAME-ENDING threshold, the outcome of THAT map is ~locked,
but the BO3 moneyline (MATCH_WINNER) book is slow to reprice the new series state —
stale quotes sit there for minutes. Buy the now-near-certain winner's ML below the
model series-fair; exit when the map ends and the book reprices.

This module only produces ENTRY signals. Exit is handled separately (sell/realize at
map-end, NOT hold-to-series-settle — a non-decider win doesn't redeem at $1).
"""
from __future__ import annotations
import os, time, uuid
from dataclasses import dataclass, field
from typing import Mapping, Any

import winprob
from config import (
    DSWING_LEAD,
    DSWING_MAX_BOOK_AGE_MS,
    DSWING_MAX_PRICE,
    DSWING_MIN_EDGE,
    DSWING_MIN_GAME_TIME,
    DSWING_MIN_P_GAME,
    DSWING_TRADE_USD,
    RUNTIME_CONFIG,
)
from execution_policy import PolicyInput, evaluate_policy, signal_policy_fields
from gettoplive_state import validate_top_live_state
import strategy_registry
try:
    from series_model import compute_bo3_match_p
except Exception:
    compute_bo3_match_p = None

DSWING_ENABLED = RUNTIME_CONFIG.strategy.dswing_enabled

_NS = uuid.UUID("11111111-2222-3333-4444-555555555557")
_sniped: set[tuple[str, str, str, str]] = set()   # (match_id, direction, token_id, current_game_number) already fired

import json
_SNIPES_FILE = "logs/dswing_snipes.json"

def _load_snipes():
    global _sniped
    if os.path.exists(_SNIPES_FILE):
        try:
            with open(_SNIPES_FILE) as f:
                data = json.load(f)
                _new_sniped = set()
                for x in data:
                    if len(x) == 2:
                        _new_sniped.add((str(x[0]), str(x[1]), "unknown", "unknown"))
                    elif len(x) == 4:
                        _new_sniped.add((str(x[0]), str(x[1]), str(x[2]), str(x[3])))
                _sniped = _new_sniped
        except Exception:
            pass

def _save_snipe(match_id, direction, token_id, current_game_number):
    _sniped.add((str(match_id), str(direction), str(token_id), str(current_game_number)))
    try:
        os.makedirs(os.path.dirname(_SNIPES_FILE), exist_ok=True)
        with open(_SNIPES_FILE, "w") as f:
            json.dump(list(_sniped), f)
    except Exception:
        pass

_load_snipes()


@dataclass(frozen=True)
class DSwingSignal:
    signal_id: str
    match_id: str
    received_at_ns: int
    direction: str
    side: str
    token_id: str
    lead: int
    game_time_sec: int
    p_game: float
    p_game_used: float
    series_fair: float
    ask: float
    edge: float
    sized_usd: float
    # aliases so live_executor.try_buy_value can consume this directly
    fair_price: float = 0.0
    book_age_ms: int = 0
    would_pass_live_gates: bool = True
    would_pass_live: bool = True
    live_skip_reason: str = ""
    paper_only_bypass: bool = False
    policy_allowed: bool | None = None
    policy_reason: str = ""
    policy_version: str = ""
    risk_tags: str = ""
    edge_type: str = field(default_factory=lambda: strategy_registry.get("DSWING").edge_type)
    target_horizon: str = field(default_factory=lambda: strategy_registry.get("DSWING").target_horizon)
    expected_hold_sec: int = field(default_factory=lambda: strategy_registry.get("DSWING").expected_hold_sec)
    entry_trigger: str = field(default_factory=lambda: strategy_registry.get("DSWING").entry_trigger)
    exit_trigger: str = field(default_factory=lambda: strategy_registry.get("DSWING").exit_trigger)
    primary_metric: str = field(default_factory=lambda: strategy_registry.get("DSWING").primary_metric)
    secondary_metric: str = field(default_factory=lambda: strategy_registry.get("DSWING").secondary_metric)
    promotion_rule: str = field(default_factory=lambda: strategy_registry.get("DSWING").promotion_rule)
    disable_rule: str = field(default_factory=lambda: strategy_registry.get("DSWING").disable_rule)


@dataclass(frozen=True)
class DSwingReject:
    match_id: str
    reason: str
    direction: str = ""
    side: str = ""
    token_id: str = ""
    lead: int | None = None
    game_time_sec: int | None = None
    ask: float | None = None
    book_age_ms: int | None = None
    p_game: float | None = None
    series_fair: float | None = None
    current_game_number: int | None = None
    would_pass_live_gates: bool = False
    live_skip_reason: str = ""


def _series_fair(mapping: Mapping, side: str, p_game: float) -> float | None:
    """Model series-win prob for the side that just (near-)won this map. Uses the
    binder's series state via compute_bo3_match_p. Rejects if state or model is missing."""
    def _i(x):
        try:
            return int(x)
        except (TypeError, ValueError):
            return None
    gn = _i(mapping.get("current_game_number")) or _i(mapping.get("game_number"))
    sy, sn = _i(mapping.get("series_score_yes")), _i(mapping.get("series_score_no"))
    if compute_bo3_match_p is None:
        return None
    if gn is None or sy is None or sn is None:
        return None
    # winner-as-yes terms
    pc_yes = p_game if side == "YES" else (1.0 - p_game)
    sy2, sn2 = (sy, sn) if side == "YES" else (sn, sy)
    try:
        p_yes_series = compute_bo3_match_p(pc_yes, 0.5, sy2, sn2, gn)
        return p_yes_series if side == "YES" else (1.0 - p_yes_series)
    except Exception:
        return None


class DecisiveSwingEngine:
    def evaluate(self, game: Mapping, mapping: Mapping, book_store: Any):
        if not DSWING_ENABLED:
            return []
        if str(mapping.get("market_type")) != "MATCH_WINNER":
            return []   # this edge is specifically the BO3 moneyline
        match_id = str(game.get("match_id") or "")
        if not match_id or game.get("data_source") != "top_live" or game.get("game_over"):
            return []
        state_check = validate_top_live_state(game)
        if not state_check.ok:
            missing = ",".join(state_check.missing_fields)
            reason = state_check.reason if not missing else f"{state_check.reason}:{missing}"
            return [DSwingReject(match_id, reason)]
        gt = game.get("game_time_sec")
        lead = game.get("radiant_lead")
        if gt is None or lead is None or gt < DSWING_MIN_GAME_TIME:
            return []
        try:
            lead = int(lead)
        except (TypeError, ValueError):
            return []
        if abs(lead) < DSWING_LEAD:
            return []                                   # not a decisive/game-ending swing
        direction = "radiant" if lead > 0 else "dire"

        sm = mapping.get("steam_side_mapping", "normal")
        if sm == "normal":
            side = "YES" if direction == "radiant" else "NO"
        elif sm == "reversed":
            side = "NO" if direction == "radiant" else "YES"
        else:
            return [DSwingReject(match_id, "unknown_side_mapping")]
        token_id = mapping.get("yes_token_id") if side == "YES" else mapping.get("no_token_id")
        if not token_id:
            return [DSwingReject(match_id, "missing_token_id")]
            
        def _i(x):
            try:
                return int(x)
            except (TypeError, ValueError):
                return None
        gn = _i(mapping.get("current_game_number")) or _i(mapping.get("game_number"))

        snipe_key = (str(match_id), str(direction), str(token_id), str(gn))
        if snipe_key in _sniped:
            return []                                   # one snipe per match-side-token-game

        book = book_store.get(token_id) if book_store else None
        ask = None
        if book:
            try:
                ask = float(book.get("best_ask"))
            except (TypeError, ValueError):
                ask = None
        def _rej(reason, **kw):
            return [DSwingReject(
                match_id=match_id, reason=reason, direction=direction, side=side, token_id=str(token_id),
                lead=lead, game_time_sec=gt, current_game_number=gn, **kw
            )]

        if ask is None:
            return _rej("missing_ask")
        rcv = book.get("received_at_ns")
        book_age_ms = int((time.time_ns() - rcv) / 1e6) if rcv else None
        if not rcv or book_age_ms > DSWING_MAX_BOOK_AGE_MS:
            return _rej("book_stale", ask=ask, book_age_ms=book_age_ms)
        if ask > DSWING_MAX_PRICE:
            return _rej("price_too_high", ask=ask, book_age_ms=book_age_ms)

        # Elo + single-game prob (~0.95 at a decisive lead), then series fair.
        from fair_value import compute_side_fair
        fair_res = compute_side_fair(game=game, side=direction)
        if not fair_res.model_available:
            return _rej(f"model_unavailable:{fair_res.model_reason}", ask=ask, book_age_ms=book_age_ms)

        p_game = fair_res.fair_used if fair_res.fair_used is not None else fair_res.fair
        
        if p_game < DSWING_MIN_P_GAME:
            return _rej(f"p_game_too_low:{p_game:.3f}", ask=ask, book_age_ms=book_age_ms, p_game=p_game)

        series_fair = _series_fair(mapping, side, p_game)
        if series_fair is None:
            return _rej("missing_series_state_or_model", ask=ask, book_age_ms=book_age_ms, p_game=p_game)
            
        edge = series_fair - ask
        if edge < DSWING_MIN_EDGE:
            return _rej(f"edge_too_small:{edge:.3f}", ask=ask, book_age_ms=book_age_ms, p_game=p_game, series_fair=series_fair)

        policy_fields = signal_policy_fields(evaluate_policy(PolicyInput(
            mode="paper_research",
            strategy_kind="DSWING",
            market_type=str(mapping.get("market_type") or ""),
            token_id=str(token_id),
            side=side,
            signal={
                "event_type": "DSWING",
                "strategy_kind": "DSWING",
                "token_id": str(token_id),
                "side": side,
                "fair_price": series_fair,
                "executable_edge": edge,
                "ask": ask,
                "max_fill_price": DSWING_MAX_PRICE,
                "target_horizon": strategy_registry.get("DSWING").target_horizon,
                "expected_hold_sec": strategy_registry.get("DSWING").expected_hold_sec,
            },
            game=dict(game),
            mapping=dict(mapping),
            book=dict(book),
            now_ns=time.time_ns(),
        )))

        _save_snipe(match_id, direction, token_id, gn)
        return [DSwingSignal(
            signal_id=str(uuid.uuid5(_NS, f"dswing|{match_id}|{direction}")),
            match_id=match_id, received_at_ns=int(game.get("received_at_ns") or 0),
            direction=direction, side=side, token_id=str(token_id),
            lead=lead, game_time_sec=gt, p_game=p_game, p_game_used=p_game, series_fair=series_fair,
            ask=ask, edge=edge, sized_usd=DSWING_TRADE_USD,
            fair_price=series_fair, book_age_ms=int((time.time_ns() - rcv) / 1e6),
            **policy_fields,
        )]
