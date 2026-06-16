from __future__ import annotations

import os
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Mapping, Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import winprob
from gettoplive_state import validate_top_live_state

VALUE_ENGINE_ENABLED = os.getenv("VALUE_ENGINE_ENABLED", "true").lower() in {"1", "true", "yes"}
ENABLE_VALUE_TRADING = os.getenv("ENABLE_VALUE_TRADING", "true").lower() in {"1", "true", "yes"}
VALUE_MIN_EDGE = float(os.getenv("VALUE_MIN_EDGE", "0.10"))
VALUE_MAX_PRICE = float(os.getenv("VALUE_MAX_PRICE", "0.84"))
VALUE_MIN_NW_LEAD = int(os.getenv("VALUE_MIN_NW_LEAD", "3000"))
VALUE_MIN_GAME_TIME = int(os.getenv("VALUE_MIN_GAME_TIME", "600"))
# Conviction floor (2026-06-03 sweep): the edge is concentrated in high-conviction
# trades. Gating on model fair (not raw lead — lead is already inside fair) moved the
# backtest from P(ROI>0)=0.89/CI-straddles-0 to 0.98/CI-off-0, win 70%→83%. Default
# 0.0 = off; set 0.80 in .env to deploy. See scripts/value_sweep.py.
VALUE_MIN_FAIR = float(os.getenv("VALUE_MIN_FAIR", "0.0"))
# Opposite-side HEDGE gate (2026-06-04, user): AFTER the first signal fires on a match,
# the OPPOSITE side may enter with LOOSER gates so a swingy/unpredictable match can
# self-offset. Only applies when we already hold the opposite token (see entered_tokens).
VALUE_HEDGE_MIN_FAIR = float(os.getenv("VALUE_HEDGE_MIN_FAIR", "0.5"))
VALUE_HEDGE_MIN_EDGE = float(os.getenv("VALUE_HEDGE_MIN_EDGE", "0.04"))
# Edge-analysis filters (2026-06-03) — remove the toxic buckets the breakdown
# exposed: ask<0.5 (flip, −75%), edge>0.30 (fake edge/model-wrong, −100%),
# late game (variance cliff, −30%). All env-tunable; set extreme to disable.
VALUE_MIN_PRICE = float(os.getenv("VALUE_MIN_PRICE", "0.50"))        # anti-flip price floor
VALUE_MAX_EDGE = float(os.getenv("VALUE_MAX_EDGE", "0.30"))          # cap fake/huge edges
VALUE_MAX_GAME_TIME = int(os.getenv("VALUE_MAX_GAME_TIME", "1800"))  # skip late-game variance
VALUE_TRADE_USD = float(os.getenv("VALUE_TRADE_USD", "5.0"))
VALUE_MAX_BOOK_AGE_MS = int(os.getenv("VALUE_MAX_BOOK_AGE_MS", "30000"))
# Orientation-flip guard (see bug_binder_orientation_flip): a binder flip routes
# us to the LOSER's token, which then looks like a screaming value buy. A genuine
# big leader's token is never dirt cheap. Mirrors live_executor.try_buy guard.
VALUE_FLIP_LEAD = int(os.getenv("VALUE_FLIP_LEAD", "5000"))
VALUE_FLIP_ASK_FLOOR = float(os.getenv("VALUE_FLIP_ASK_FLOOR", "0.35"))

_NAMESPACE = uuid.UUID("11111111-2222-3333-4444-555555555556")

def _make_signal_id(match_id: str, received_at_ns: int) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"value|{match_id}|{received_at_ns}"))

# Rolling radiant-lead history per match → net-worth-lead trajectory (slope).
_SLOPE_WINDOW_NS = 300 * 1_000_000_000          # 5-minute window
_lead_hist: dict[str, deque] = {}

def _lead_slope(match_id: str, radiant_lead: int, now_ns: int) -> float:
    """Change in radiant net-worth lead over the trailing ~5 min. 0.0 until enough
    history exists. A growing leader's lead → positive (in radiant perspective)."""
    dq = _lead_hist.setdefault(match_id, deque(maxlen=4000))
    if not dq or dq[-1][0] != now_ns:
        dq.append((now_ns, int(radiant_lead)))
    cutoff = now_ns - int(_SLOPE_WINDOW_NS * 1.5)
    while dq and dq[0][0] < cutoff:
        dq.popleft()
    target = now_ns - _SLOPE_WINDOW_NS
    past = None
    for ns, ld in dq:
        if ns <= target:
            past = ld
        else:
            break
    return 0.0 if past is None else float(radiant_lead - past)

@dataclass(frozen=True)
class ValueSignal:
    signal_id: str
    match_id: str
    received_at_ns: int
    direction: str
    side: str
    token_id: str
    fair_price: float
    ask: float
    edge: float
    lead: int
    game_time_sec: int
    elo_diff: float | None
    sized_usd: float
    book_age_ms: int

    def to_signal_dict(self) -> dict:
        return {
            "signal_id": self.signal_id,
            "match_id": self.match_id,
            "decision": "paper_buy_yes",
            "reason": "value_edge",
            "token_id": self.token_id,
            "side": self.side,
            "fair_price": self.fair_price,
            "executable_edge": self.edge,
            "expected_move": 0.0,
            "target_size_usd": self.sized_usd,
            "size_multiplier": 1.0,
            "event_type": "VALUE_HOLD",
            "event_tier": "A",
            "event_is_primary": True,
            "event_family": "VALUE",
            "event_quality": 1.0,
            "event_direction": self.direction,
        }

@dataclass(frozen=True)
class ValueReject:
    match_id: str
    received_at_ns: int
    reason: str
    direction: str = ""
    side: str = ""
    token_id: str = ""
    fair_price: float | None = None
    ask: float | None = None
    edge: float | None = None
    lead: int | None = None
    game_time_sec: int | None = None
    elo_diff: float | None = None
    book_age_ms: int | None = None

class ValueEngine:
    def evaluate(self, game: Mapping, mapping: Mapping, book_store: Any, entered_tokens: Any = None) -> list[ValueSignal | ValueReject]:
        if not VALUE_ENGINE_ENABLED:
            return []

        match_id = str(game.get("match_id") or "")
        if not match_id:
            return []

        # Only process top_live updates (skip slow league stream updates)
        if game.get("data_source") != "top_live":
            return []

        cur_ns = int(game.get("received_at_ns") or 0)
        state_check = validate_top_live_state(game)
        if not state_check.ok:
            missing = ",".join(state_check.missing_fields)
            reason = state_check.reason if not missing else f"{state_check.reason}:{missing}"
            return [ValueReject(match_id, cur_ns, reason)]

        # Skip finished games (a settled/ending game can linger in top_live with a
        # stale lead and trigger a phantom trade).
        if game.get("game_over"):
            return [ValueReject(match_id, cur_ns, "game_over")]
        
        # 1. Basic sanity checks
        game_time = game.get("game_time_sec")
        if game_time is None:
            return [ValueReject(match_id, cur_ns, "missing_game_time")]
        if game_time < VALUE_MIN_GAME_TIME:
            return [ValueReject(match_id, cur_ns, "game_too_early", game_time_sec=game_time)]
        if game_time > VALUE_MAX_GAME_TIME:
            # late-game variance cliff: leads convert far worse (comebacks/buybacks)
            return [ValueReject(match_id, cur_ns, "game_too_late", game_time_sec=game_time)]

        lead = game.get("radiant_lead")
        if lead is None:
            return [ValueReject(match_id, cur_ns, "missing_lead", game_time_sec=game_time)]
        try:
            lead = int(lead)
        except (TypeError, ValueError):
            return [ValueReject(match_id, cur_ns, "invalid_lead", game_time_sec=game_time)]

        # 2. Determine who is leading
        if abs(lead) < VALUE_MIN_NW_LEAD:
            return [ValueReject(match_id, cur_ns, "lead_too_small", lead=lead, game_time_sec=game_time)]

        direction = "radiant" if lead > 0 else "dire"

        # 2b. Market-type gate. The win-prob `fair` is a SINGLE-GAME probability.
        # MAP_WINNER settles on this game → fair is correct. MATCH_WINNER settles
        # on the BO3 series → fair is WRONG (a game-1 leader wins the game ~0.90
        # but the series only ~0.62) UNLESS this is a game-3 decider, where winning
        # the game == winning the series. Skip non-proxy series markets rather than
        # systematically overpay them.
        market_type = str(mapping.get("market_type") or "").upper()
        if market_type == "MATCH_WINNER":
            try:
                from market_scope import is_game3_match_proxy
                _is_g3 = is_game3_match_proxy(mapping)
            except Exception:
                _is_g3 = False
            if not _is_g3:
                return [ValueReject(match_id, cur_ns, "series_market_unpriced",
                                    direction=direction, lead=lead, game_time_sec=game_time)]
        elif market_type != "MAP_WINNER":
            return [ValueReject(match_id, cur_ns, "unsupported_market_type",
                                direction=direction, lead=lead, game_time_sec=game_time)]

        # 3. Side & Token Mapping
        side_map = mapping.get("steam_side_mapping", "normal")
        if side_map == "normal":
            side = "YES" if direction == "radiant" else "NO"
        elif side_map == "reversed":
            side = "NO" if direction == "radiant" else "YES"
        else:
            return [ValueReject(match_id, cur_ns, "unknown_side_mapping", direction=direction, lead=lead, game_time_sec=game_time)]

        token_id = mapping.get("yes_token_id") if side == "YES" else mapping.get("no_token_id")
        if not token_id:
            return [ValueReject(match_id, cur_ns, "missing_token_id", direction=direction, side=side, lead=lead, game_time_sec=game_time)]

        # 4. Get the book
        book = book_store.get(token_id) if book_store else None
        if not book:
            return [ValueReject(match_id, cur_ns, "missing_book", direction=direction, side=side, token_id=token_id, lead=lead, game_time_sec=game_time)]
        
        try:
            ask = float(book.get("best_ask"))
        except (TypeError, ValueError):
            return [ValueReject(match_id, cur_ns, "missing_ask", direction=direction, side=side, token_id=token_id, lead=lead, game_time_sec=game_time)]
            
        received_at_ns = book.get("received_at_ns")
        if not received_at_ns:
            # No timestamp → can't prove the ask is fresh/fillable. Treat as stale.
            return [ValueReject(
                match_id, cur_ns, "book_no_timestamp",
                direction=direction, side=side, token_id=token_id,
                ask=ask, lead=lead, game_time_sec=game_time
            )]
        book_age_ms = int((time.time_ns() - received_at_ns) / 1_000_000)

        if book_age_ms > VALUE_MAX_BOOK_AGE_MS:
            # We want to print this specifically for book feed verification (#2)
            print(f"VALUE_ENGINE_STALE_BOOK: token={token_id} age={book_age_ms}ms")
            return [ValueReject(
                match_id, cur_ns, "book_stale",
                direction=direction, side=side, token_id=token_id,
                ask=ask, lead=lead, game_time_sec=game_time, book_age_ms=book_age_ms
            )]

        if ask > VALUE_MAX_PRICE:
            return [ValueReject(
                match_id, cur_ns, "price_too_high",
                direction=direction, side=side, token_id=token_id,
                ask=ask, lead=lead, game_time_sec=game_time, book_age_ms=book_age_ms
            )]
        if ask < VALUE_MIN_PRICE:
            # anti-flip: a genuine net-worth leader's token is a favorite. If it's
            # priced below the floor the market disagrees with our side → skip.
            return [ValueReject(
                match_id, cur_ns, "price_too_low",
                direction=direction, side=side, token_id=token_id,
                ask=ask, lead=lead, game_time_sec=game_time, book_age_ms=book_age_ms
            )]

        # 4b. Orientation-flip guard. We only reach here backing the net-worth
        # LEADER. If a binder flip bound this side to the trailing team's token,
        # that token is cheap → fair−ask looks huge → we'd buy the LOSER and
        # settle $0, silently. A genuine big leader's token is never this cheap, so
        # reject when a strong lead contradicts a dirt-cheap ask. (See live_executor
        # try_buy guard / bug_binder_orientation_flip.)
        if abs(lead) > VALUE_FLIP_LEAD and ask < VALUE_FLIP_ASK_FLOOR:
            print(f"VALUE_ENGINE_FLIP_SUSPECTED: match={match_id} lead={lead} side={side} token={token_id} ask={ask}")
            return [ValueReject(
                match_id, cur_ns, "orientation_flip_suspected",
                direction=direction, side=side, token_id=token_id,
                ask=ask, lead=lead, game_time_sec=game_time, book_age_ms=book_age_ms
            )]

        # 5. Compute fair price (LEADER perspective). Elo resolves by team_id OR
        # name (feed gives id ~3% of the time, name almost always). Trajectory =
        # leader's lead change /5min. Draft-H2H = leader's hero-matchup advantage.
        rtid, dtid = game.get("radiant_team_id"), game.get("dire_team_id")
        rname, dname = game.get("radiant_team"), game.get("dire_team")
        slope_rad = _lead_slope(match_id, lead, cur_ns)   # radiant perspective
        players = game.get("players") or []
        rad_heroes = [p.get("hero_id") for p in players if p.get("team") == 0]
        dire_heroes = [p.get("hero_id") for p in players if p.get("team") == 1]
        draft_rad = winprob.draft_h2h(rad_heroes, dire_heroes)   # radiant perspective, or None
        if direction == "radiant":
            elo_diff = winprob.elo_diff(rtid, dtid, rname, dname)
            lead_slope = slope_rad
            draft = draft_rad
        else:
            elo_diff = winprob.elo_diff(dtid, rtid, dname, rname)
            lead_slope = -slope_rad
            draft = (-draft_rad) if draft_rad is not None else None

        fair_price = winprob.fair(abs(lead), game_time, elo_diff, lead_slope, draft)
        edge = fair_price - ask

        # Tiered gate: if we already hold the OPPOSITE token on this match, this is the
        # offset/hedge entry -> looser gate (fair>0.5, edge>=0.04). Else the primary gate.
        _et = {str(t) for t in (entered_tokens or [])}
        _opp_tok = str(mapping.get("no_token_id") if str(token_id) == str(mapping.get("yes_token_id")) else mapping.get("yes_token_id"))
        _is_hedge = bool(_et) and _opp_tok in _et
        _min_fair = VALUE_HEDGE_MIN_FAIR if _is_hedge else VALUE_MIN_FAIR
        _min_edge = VALUE_HEDGE_MIN_EDGE if _is_hedge else VALUE_MIN_EDGE
        if fair_price < _min_fair:
            return [ValueReject(
                match_id, cur_ns, "fair_too_low",
                direction=direction, side=side, token_id=token_id,
                fair_price=fair_price, ask=ask, edge=edge,
                lead=lead, game_time_sec=game_time, elo_diff=elo_diff, book_age_ms=book_age_ms
            )]
        if edge < _min_edge:
            return [ValueReject(
                match_id, cur_ns, "edge_too_small",
                direction=direction, side=side, token_id=token_id,
                fair_price=fair_price, ask=ask, edge=edge,
                lead=lead, game_time_sec=game_time, elo_diff=elo_diff, book_age_ms=book_age_ms
            )]
        if edge > VALUE_MAX_EDGE:
            # a huge edge means the model wildly disagrees with a liquid market →
            # the MODEL is wrong (or orientation flip), not the market. Skip.
            return [ValueReject(
                match_id, cur_ns, "edge_too_large",
                direction=direction, side=side, token_id=token_id,
                fair_price=fair_price, ask=ask, edge=edge,
                lead=lead, game_time_sec=game_time, elo_diff=elo_diff, book_age_ms=book_age_ms
            )]

        # 6. We have a signal!
        signal = ValueSignal(
            signal_id=_make_signal_id(match_id, cur_ns),
            match_id=match_id,
            received_at_ns=cur_ns,
            direction=direction,
            side=side,
            token_id=token_id,
            fair_price=fair_price,
            ask=ask,
            edge=edge,
            lead=lead,
            game_time_sec=game_time,
            elo_diff=elo_diff,
            sized_usd=VALUE_TRADE_USD,
            book_age_ms=book_age_ms,
        )
        return [signal]
