from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from market_scope import is_game3_match_proxy
except Exception:

    def is_game3_match_proxy(mapping: dict) -> bool:
        return False


FAVORITE_ENGINE_ENABLED = os.getenv("FAVORITE_ENGINE_ENABLED", "false").lower() in {"1", "true", "yes"}
ENABLE_FAVORITE_TRADING = os.getenv("ENABLE_FAVORITE_TRADING", "false").lower() in {"1", "true", "yes"}
FAVORITE_MIN_GAME_TIME = int(os.getenv("FAVORITE_MIN_GAME_TIME", "600"))
FAVORITE_MAX_GAME_TIME = int(os.getenv("FAVORITE_MAX_GAME_TIME", "2400"))
FAVORITE_MAX_BOOK_AGE_MS = int(os.getenv("FAVORITE_MAX_BOOK_AGE_MS", "15000"))
FAVORITE_MIN_ASK = float(os.getenv("FAVORITE_MIN_ASK", "0.50"))
FAVORITE_MAX_ASK = float(os.getenv("FAVORITE_MAX_ASK", "0.80"))
FAVORITE_MAX_ABS_LEAD = int(os.getenv("FAVORITE_MAX_ABS_LEAD", "3000"))
FAVORITE_TRADE_USD = float(os.getenv("FAVORITE_TRADE_USD", "20.0"))

_NAMESPACE = uuid.UUID("11111111-2222-3333-4444-555555555557")


def _make_signal_id(match_id: str, received_at_ns: int) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"favorite|{match_id}|{received_at_ns}"))


@dataclass(frozen=True)
class FavoriteSignal:
    signal_id: str
    match_id: str
    received_at_ns: int
    side: str
    token_id: str
    ask: float
    other_ask: float
    book_prob: float
    book_gap: float
    lead: int
    game_time_sec: int
    book_age_ms: int
    sized_usd: float

    @property
    def direction(self) -> str:
        return "book_yes" if self.side == "YES" else "book_no"

    @property
    def fair_price(self) -> float:
        return self.book_prob

    @property
    def edge(self) -> float:
        return self.book_gap

    @property
    def elo_diff(self) -> None:
        return None

    def to_signal_dict(self) -> dict:
        return {
            "signal_id": self.signal_id,
            "match_id": self.match_id,
            "decision": "paper_buy_yes",
            "reason": "book_favorite_settlement",
            "token_id": self.token_id,
            "side": self.side,
            "fair_price": self.book_prob,
            "ask": self.ask,
            "executable_edge": self.book_gap,
            "expected_move": 0.0,
            "target_size_usd": self.sized_usd,
            "size_multiplier": 1.0,
            "event_type": "BOOK_FAVORITE_SETTLEMENT",
            "event_tier": "A",
            "event_is_primary": True,
            "event_family": "FAVORITE",
            "event_quality": 1.0,
            "event_direction": self.direction,
        }


@dataclass(frozen=True)
class FavoriteReject:
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


class FavoriteEngine:
    """One-shot book-favorite settlement candidate.

    Backtest route: first eligible snapshot per match, buy higher-ask token,
    hold to settlement. This engine only emits one signal per match.
    """

    def __init__(self) -> None:
        self._seen_matches: set[str] = set()

    def evaluate(self, game: Mapping[str, Any], mapping: Mapping[str, Any], book_store: Any) -> list[FavoriteSignal | FavoriteReject]:
        if not FAVORITE_ENGINE_ENABLED:
            return []

        match_id = str(game.get("match_id") or "")
        if not match_id or match_id in self._seen_matches:
            return []
        if game.get("data_source") != "top_live":
            return []

        cur_ns = int(game.get("received_at_ns") or time.time_ns())
        if game.get("game_over"):
            return [FavoriteReject(match_id, cur_ns, "game_over")]

        game_time = game.get("game_time_sec")
        if game_time is None:
            return [FavoriteReject(match_id, cur_ns, "missing_game_time")]
        game_time = int(game_time)
        if game_time < FAVORITE_MIN_GAME_TIME:
            return []
        if game_time > FAVORITE_MAX_GAME_TIME:
            self._seen_matches.add(match_id)
            return [FavoriteReject(match_id, cur_ns, "game_too_late", game_time_sec=game_time)]

        lead = game.get("radiant_lead")
        if lead is None:
            return [FavoriteReject(match_id, cur_ns, "missing_lead", game_time_sec=game_time)]
        try:
            lead = int(lead)
        except (TypeError, ValueError):
            return [FavoriteReject(match_id, cur_ns, "invalid_lead", game_time_sec=game_time)]
        if abs(lead) > FAVORITE_MAX_ABS_LEAD:
            self._seen_matches.add(match_id)
            return [FavoriteReject(match_id, cur_ns, "lead_too_large", lead=lead, game_time_sec=game_time)]

        market_type = str(mapping.get("market_type") or "").upper()
        if market_type == "MATCH_WINNER" and not is_game3_match_proxy(dict(mapping)):
            self._seen_matches.add(match_id)
            return [FavoriteReject(match_id, cur_ns, "series_market_unpriced", game_time_sec=game_time)]
        if market_type not in {"MAP_WINNER", "MATCH_WINNER"}:
            self._seen_matches.add(match_id)
            return [FavoriteReject(match_id, cur_ns, "unsupported_market_type", game_time_sec=game_time)]

        yes_token = str(mapping.get("yes_token_id") or "")
        no_token = str(mapping.get("no_token_id") or "")
        yes_book = book_store.get(yes_token) if book_store and yes_token else None
        no_book = book_store.get(no_token) if book_store and no_token else None
        if not yes_book or not no_book:
            return [FavoriteReject(match_id, cur_ns, "missing_book", game_time_sec=game_time)]

        try:
            yes_ask = float(yes_book.get("best_ask"))
            no_ask = float(no_book.get("best_ask"))
        except (TypeError, ValueError):
            return [FavoriteReject(match_id, cur_ns, "missing_ask", game_time_sec=game_time)]

        yes_ns = int(yes_book.get("received_at_ns") or 0)
        no_ns = int(no_book.get("received_at_ns") or 0)
        book_age_ms = max(int((time.time_ns() - yes_ns) / 1_000_000), int((time.time_ns() - no_ns) / 1_000_000))
        if book_age_ms > FAVORITE_MAX_BOOK_AGE_MS:
            return [FavoriteReject(match_id, cur_ns, "book_stale", game_time_sec=game_time, book_age_ms=book_age_ms)]

        if yes_ask >= no_ask:
            side, token_id, ask, other_ask = "YES", yes_token, yes_ask, no_ask
        else:
            side, token_id, ask, other_ask = "NO", no_token, no_ask, yes_ask

        if ask < FAVORITE_MIN_ASK:
            return [FavoriteReject(match_id, cur_ns, "price_too_low", side=side, token_id=token_id, ask=ask, game_time_sec=game_time, book_age_ms=book_age_ms)]
        if ask > FAVORITE_MAX_ASK:
            return [FavoriteReject(match_id, cur_ns, "price_too_high", side=side, token_id=token_id, ask=ask, game_time_sec=game_time, book_age_ms=book_age_ms)]

        self._seen_matches.add(match_id)
        book_sum = max(ask + other_ask, 1e-9)
        book_prob = ask / book_sum
        return [
            FavoriteSignal(
                signal_id=_make_signal_id(match_id, cur_ns),
                match_id=match_id,
                received_at_ns=cur_ns,
                side=side,
                token_id=token_id,
                ask=ask,
                other_ask=other_ask,
                book_prob=book_prob,
                book_gap=abs(ask - other_ask),
                lead=lead,
                game_time_sec=game_time,
                book_age_ms=book_age_ms,
                sized_usd=FAVORITE_TRADE_USD,
            )
        ]
