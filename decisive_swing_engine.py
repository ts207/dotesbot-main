"""Decisive-swing ML sniper (2026-06-03).

Edge (validated: +8c convergence, 82% positive on n=34): when a Dota game's
net-worth lead crosses a GAME-ENDING threshold, the outcome of THAT map is ~locked,
but the BO3 moneyline (MATCH_WINNER) book is slow to reprice the new series state —
stale quotes sit there for minutes. Buy the now-near-certain winner's ML below the
model series-fair; exit when the map ends and the book reprices.

This module only produces ENTRY signals. Exit is handled separately (sell/realize at
map-end, NOT hold-to-series-settle — a non-decider win doesn't redeem at $1).

NOT yet wired into main.py — entry logic + backtest are done; wiring the map-end exit
is the remaining step (do it when there's capital to trade).
"""
from __future__ import annotations
import os, time, uuid
from dataclasses import dataclass
from typing import Mapping, Any

import winprob
from gettoplive_state import validate_top_live_state
try:
    from series_model import compute_bo3_match_p
except Exception:
    compute_bo3_match_p = None

DSWING_ENABLED = os.getenv("DSWING_ENABLED", "false").lower() in {"1", "true", "yes"}
DSWING_LEAD = int(os.getenv("DSWING_LEAD", "6000"))          # game-ending swing; backtest: enter EARLY (6k) = +14.8% vs +0.7% at 12k
DSWING_MIN_EDGE = float(os.getenv("DSWING_MIN_EDGE", "0.05"))  # series_fair - ask
DSWING_MAX_PRICE = float(os.getenv("DSWING_MAX_PRICE", "0.92"))
DSWING_MIN_GAME_TIME = int(os.getenv("DSWING_MIN_GAME_TIME", "600"))
DSWING_TRADE_USD = float(os.getenv("DSWING_TRADE_USD", "5.0"))
DSWING_MAX_BOOK_AGE_MS = int(os.getenv("DSWING_MAX_BOOK_AGE_MS", "15000"))
DSWING_MIN_P_GAME = float(os.getenv("DSWING_MIN_P_GAME", "0.88"))

_NS = uuid.UUID("11111111-2222-3333-4444-555555555557")
_sniped: set[tuple[str, str]] = set()   # (match_id, direction) already fired

import json
_SNIPES_FILE = "logs/dswing_snipes.json"

def _load_snipes():
    global _sniped
    if os.path.exists(_SNIPES_FILE):
        try:
            with open(_SNIPES_FILE) as f:
                data = json.load(f)
                _sniped = {tuple(x) for x in data}
        except Exception:
            pass

def _save_snipe(match_id, direction):
    _sniped.add((match_id, direction))
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
    series_fair: float
    ask: float
    edge: float
    sized_usd: float
    # aliases so live_executor.try_buy_value can consume this directly
    fair_price: float = 0.0
    book_age_ms: int = 0


@dataclass(frozen=True)
class DSwingReject:
    match_id: str
    reason: str


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
        if (match_id, direction) in _sniped:
            return []                                   # one snipe per match-side

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

        book = book_store.get(token_id) if book_store else None
        ask = None
        if book:
            try:
                ask = float(book.get("best_ask"))
            except (TypeError, ValueError):
                ask = None
        if ask is None:
            return [DSwingReject(match_id, "missing_ask")]
        rcv = book.get("received_at_ns")
        if not rcv or (time.time_ns() - rcv) / 1e6 > DSWING_MAX_BOOK_AGE_MS:
            return [DSwingReject(match_id, "book_stale")]
        if ask > DSWING_MAX_PRICE:
            return [DSwingReject(match_id, "price_too_high")]

        # Elo + single-game prob (~0.95 at a decisive lead), then series fair.
        from fair_value import compute_side_fair
        fair_res = compute_side_fair(game=game, side=direction)
        p_game = fair_res.fair
        
        if p_game < DSWING_MIN_P_GAME:
            return [DSwingReject(match_id, f"p_game_too_low:{p_game:.3f}")]

        series_fair = _series_fair(mapping, side, p_game)
        if series_fair is None:
            return [DSwingReject(match_id, "missing_series_state_or_model")]
            
        edge = series_fair - ask
        if edge < DSWING_MIN_EDGE:
            return [DSwingReject(match_id, f"edge_too_small:{edge:.3f}")]

        _save_snipe(match_id, direction)
        return [DSwingSignal(
            signal_id=str(uuid.uuid5(_NS, f"dswing|{match_id}|{direction}")),
            match_id=match_id, received_at_ns=int(game.get("received_at_ns") or 0),
            direction=direction, side=side, token_id=str(token_id),
            lead=lead, game_time_sec=gt, p_game=p_game, series_fair=series_fair,
            ask=ask, edge=edge, sized_usd=DSWING_TRADE_USD,
            fair_price=series_fair, book_age_ms=int((time.time_ns() - rcv) / 1e6),
        )]
