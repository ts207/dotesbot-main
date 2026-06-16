from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, asdict
from typing import Any, Mapping

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import winprob
from market_scope import is_game3_match_proxy


EARLY_FAVORITE_SHADOW_ENABLED = os.getenv("EARLY_FAVORITE_SHADOW_ENABLED", "false").lower() in {"1", "true", "yes"}
EARLY_FAVORITE_SHADOW_STAKE_USD = float(os.getenv("EARLY_FAVORITE_SHADOW_STAKE_USD", "20.0"))
EARLY_FAVORITE_MAX_BOOK_AGE_MS = int(os.getenv("EARLY_FAVORITE_MAX_BOOK_AGE_MS", "15000"))
EARLY_FAVORITE_MIN_GAME_TIME = int(os.getenv("EARLY_FAVORITE_MIN_GAME_TIME", "300"))
EARLY_FAVORITE_MAX_GAME_TIME = int(os.getenv("EARLY_FAVORITE_MAX_GAME_TIME", "900"))
EARLY_FAVORITE_LATE_NO_ENTRY_SEC = int(os.getenv("EARLY_FAVORITE_LATE_NO_ENTRY_SEC", "1800"))
EARLY_FAVORITE_HEDGE_MIN_FAIR = float(os.getenv("EARLY_FAVORITE_HEDGE_MIN_FAIR", "0.50"))
EARLY_FAVORITE_HEDGE_MIN_EDGE = float(os.getenv("EARLY_FAVORITE_HEDGE_MIN_EDGE", "0.04"))

_NAMESPACE = uuid.UUID("11111111-2222-3333-4444-555555555558")


def _signal_id(strategy_id: str, match_id: str, ns: int) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{strategy_id}|{match_id}|{ns}"))


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _side_to_token(mapping: Mapping[str, Any], side: str) -> str:
    return str(mapping.get("yes_token_id") if side == "YES" else mapping.get("no_token_id"))


def _opposite(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def _radiant_side(mapping: Mapping[str, Any], direction: str) -> str | None:
    side_map = str(mapping.get("steam_side_mapping") or "normal")
    if side_map == "normal":
        return "YES" if direction == "radiant" else "NO"
    if side_map == "reversed":
        return "NO" if direction == "radiant" else "YES"
    return None


def _book_age_ms(book: Mapping[str, Any] | None, cur_ns: int) -> int | None:
    if not book:
        return None
    ns = int(book.get("received_at_ns") or 0)
    if not ns:
        return None
    return max(0, int((cur_ns - ns) / 1_000_000))


def _book_mid(book: Mapping[str, Any] | None) -> float | None:
    if not book:
        return None
    bid = _to_float(book.get("best_bid"))
    ask = _to_float(book.get("best_ask"))
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def _side_fair(game: Mapping[str, Any], mapping: Mapping[str, Any], side: str) -> float | None:
    lead = _to_int(game.get("radiant_lead"))
    game_time = _to_int(game.get("game_time_sec"))
    if lead is None or game_time is None:
        return None

    yes_is_radiant = _radiant_side(mapping, "radiant") == "YES"
    side_is_radiant = (side == "YES" and yes_is_radiant) or (side == "NO" and not yes_is_radiant)
    side_lead = lead if side_is_radiant else -lead
    if side_is_radiant:
        elo = winprob.elo_diff(
            game.get("radiant_team_id"),
            game.get("dire_team_id"),
            game.get("radiant_team"),
            game.get("dire_team"),
        )
    else:
        elo = winprob.elo_diff(
            game.get("dire_team_id"),
            game.get("radiant_team_id"),
            game.get("dire_team"),
            game.get("radiant_team"),
        )
    if side_lead >= 0:
        return winprob.fair(abs(side_lead), game_time, elo, None, None)
    opp_elo = None if elo is None else -float(elo)
    return 1.0 - winprob.fair(abs(side_lead), game_time, opp_elo, None, None)


def _model_side(game: Mapping[str, Any], mapping: Mapping[str, Any]) -> str | None:
    yes_fair = _side_fair(game, mapping, "YES")
    no_fair = _side_fair(game, mapping, "NO")
    if yes_fair is None or no_fair is None:
        return None
    return "YES" if yes_fair >= no_fair else "NO"


@dataclass
class EarlyFavoriteShadowEntry:
    timestamp_utc: str
    received_at_ns: int
    signal_id: str
    strategy_id: str
    match_id: str
    market_name: str
    market_type: str
    side: str
    token_id: str
    ask: float
    other_ask: float
    bid: float | None
    book_prob: float
    book_gap: float
    fair_price: float | None
    edge: float | None
    game_time_sec: int
    lead: int
    networth_side: str
    model_side: str
    book_age_ms: int
    sized_usd: float
    reject_reason: str = ""
    would_enter: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EarlyFavoriteHealth:
    timestamp_utc: str
    received_at_ns: int
    strategy_id: str
    match_id: str
    market_name: str
    position_side: str
    position_token_id: str
    entry_ask: float
    entry_game_time_sec: int
    game_time_sec: int
    health_state: str
    reason: str
    book_favorite_side: str
    networth_side: str
    model_side: str
    current_bid: float | None
    opposite_ask: float | None
    opposite_fair: float | None
    opposite_edge: float | None
    lead: int | None
    book_age_ms: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _ShadowPosition:
    strategy_id: str
    match_id: str
    market_name: str
    side: str
    token_id: str
    entry_ask: float
    entry_game_time_sec: int
    entry_ns: int


class EarlyFavoriteShadowEngine:
    """Shadow-only early-favorite settlement monitor.

    It never submits orders. It logs candidate entries and live health states so
    the settlement strategy can be validated before any live config change.
    """

    STRATEGY_A = "EARLY_FAV_A_50_75"
    STRATEGY_B = "EARLY_FAV_B_65_84_CONFIRM"

    def __init__(self) -> None:
        self._entered: set[tuple[str, str]] = set()
        self._positions: dict[tuple[str, str], _ShadowPosition] = {}
        self._last_health_bucket: dict[tuple[str, str], int] = {}
        self._closed: set[tuple[str, str]] = set()

    def observe(
        self,
        game: Mapping[str, Any],
        mapping: Mapping[str, Any],
        book_store: Any,
        *,
        entry_logger: Any | None = None,
        health_logger: Any | None = None,
    ) -> None:
        if not EARLY_FAVORITE_SHADOW_ENABLED:
            return
        match_id = str(game.get("match_id") or "")
        if not match_id or game.get("data_source") != "top_live":
            return
        market_type = str(mapping.get("market_type") or "").upper()
        if market_type == "MATCH_WINNER" and not is_game3_match_proxy(dict(mapping)):
            return
        if market_type not in {"MAP_WINNER", "MATCH_WINNER"}:
            return

        cur_ns = int(game.get("received_at_ns") or time.time_ns())
        if game.get("game_over"):
            self._log_health(game, mapping, book_store, cur_ns, health_logger, forced_state="SETTLED")
            return

        self._log_health(game, mapping, book_store, cur_ns, health_logger)
        self._maybe_enter(game, mapping, book_store, cur_ns, entry_logger)

    def _maybe_enter(
        self,
        game: Mapping[str, Any],
        mapping: Mapping[str, Any],
        book_store: Any,
        cur_ns: int,
        entry_logger: Any | None,
    ) -> None:
        game_time = _to_int(game.get("game_time_sec"))
        lead = _to_int(game.get("radiant_lead"))
        match_id = str(game.get("match_id") or "")
        if game_time is None or lead is None:
            return
        if game_time > EARLY_FAVORITE_LATE_NO_ENTRY_SEC:
            self._closed.add((self.STRATEGY_A, match_id))
            self._closed.add((self.STRATEGY_B, match_id))
            return
        if game_time < EARLY_FAVORITE_MIN_GAME_TIME or game_time > EARLY_FAVORITE_MAX_GAME_TIME:
            return

        yes_book = book_store.get(str(mapping.get("yes_token_id"))) if book_store else None
        no_book = book_store.get(str(mapping.get("no_token_id"))) if book_store else None
        yes_ask = _to_float((yes_book or {}).get("best_ask"))
        no_ask = _to_float((no_book or {}).get("best_ask"))
        if yes_ask is None or no_ask is None:
            return
        yes_age = _book_age_ms(yes_book, cur_ns)
        no_age = _book_age_ms(no_book, cur_ns)
        if yes_age is None or no_age is None:
            return
        book_age_ms = max(yes_age, no_age)
        if book_age_ms > EARLY_FAVORITE_MAX_BOOK_AGE_MS:
            return

        if yes_ask >= no_ask:
            fav_side, ask, other_ask, fav_book = "YES", yes_ask, no_ask, yes_book
        else:
            fav_side, ask, other_ask, fav_book = "NO", no_ask, yes_ask, no_book
        networth_side = _radiant_side(mapping, "radiant" if lead >= 0 else "dire") or ""
        model_side = _model_side(game, mapping) or ""
        fair = _side_fair(game, mapping, fav_side)
        edge = None if fair is None else fair - ask
        book_prob = ask / max(ask + other_ask, 1e-9)
        bid = _to_float((fav_book or {}).get("best_bid"))

        strategies = [
            (self.STRATEGY_A, 0.50 <= ask <= 0.75),
            (self.STRATEGY_B, 0.65 <= ask <= 0.84 and fav_side == networth_side and fav_side == model_side),
        ]
        for strategy_id, qualifies in strategies:
            key = (strategy_id, match_id)
            if key in self._entered or key in self._closed:
                continue
            if not qualifies:
                continue
            self._entered.add(key)
            pos = _ShadowPosition(
                strategy_id=strategy_id,
                match_id=match_id,
                market_name=str(mapping.get("name") or ""),
                side=fav_side,
                token_id=_side_to_token(mapping, fav_side),
                entry_ask=ask,
                entry_game_time_sec=game_time,
                entry_ns=cur_ns,
            )
            self._positions[key] = pos
            if entry_logger is not None:
                entry_logger.log_entry(EarlyFavoriteShadowEntry(
                    timestamp_utc="",
                    received_at_ns=cur_ns,
                    signal_id=_signal_id(strategy_id, match_id, cur_ns),
                    strategy_id=strategy_id,
                    match_id=match_id,
                    market_name=str(mapping.get("name") or ""),
                    market_type=str(mapping.get("market_type") or ""),
                    side=fav_side,
                    token_id=pos.token_id,
                    ask=ask,
                    other_ask=other_ask,
                    bid=bid,
                    book_prob=book_prob,
                    book_gap=abs(ask - other_ask),
                    fair_price=fair,
                    edge=edge,
                    game_time_sec=game_time,
                    lead=lead,
                    networth_side=networth_side,
                    model_side=model_side,
                    book_age_ms=book_age_ms,
                    sized_usd=EARLY_FAVORITE_SHADOW_STAKE_USD,
                ))

    def _log_health(
        self,
        game: Mapping[str, Any],
        mapping: Mapping[str, Any],
        book_store: Any,
        cur_ns: int,
        health_logger: Any | None,
        *,
        forced_state: str | None = None,
    ) -> None:
        if health_logger is None:
            return
        match_id = str(game.get("match_id") or "")
        game_time = _to_int(game.get("game_time_sec"))
        if game_time is None:
            return
        bucket = game_time // 60
        for key, pos in list(self._positions.items()):
            if pos.match_id != match_id:
                continue
            if forced_state is None and self._last_health_bucket.get(key) == bucket:
                continue
            self._last_health_bucket[key] = bucket

            lead = _to_int(game.get("radiant_lead"))
            yes_book = book_store.get(str(mapping.get("yes_token_id"))) if book_store else None
            no_book = book_store.get(str(mapping.get("no_token_id"))) if book_store else None
            yes_ask = _to_float((yes_book or {}).get("best_ask"))
            no_ask = _to_float((no_book or {}).get("best_ask"))
            yes_age = _book_age_ms(yes_book, cur_ns)
            no_age = _book_age_ms(no_book, cur_ns)
            book_age_ms = max([x for x in [yes_age, no_age] if x is not None], default=None)
            if yes_ask is not None and no_ask is not None:
                book_favorite_side = "YES" if yes_ask >= no_ask else "NO"
            else:
                book_favorite_side = ""
            networth_side = _radiant_side(mapping, "radiant" if (lead or 0) >= 0 else "dire") or ""
            model_side = _model_side(game, mapping) or ""

            own_book = yes_book if pos.side == "YES" else no_book
            opp_side = _opposite(pos.side)
            opp_book = no_book if pos.side == "YES" else yes_book
            current_bid = _to_float((own_book or {}).get("best_bid"))
            opposite_ask = _to_float((opp_book or {}).get("best_ask"))
            opposite_fair = _side_fair(game, mapping, opp_side)
            opposite_edge = None
            if opposite_fair is not None and opposite_ask is not None:
                opposite_edge = opposite_fair - opposite_ask

            state, reason = self._health_state(
                pos=pos,
                game_time=game_time,
                book_favorite_side=book_favorite_side,
                networth_side=networth_side,
                model_side=model_side,
                opposite_fair=opposite_fair,
                opposite_edge=opposite_edge,
                forced_state=forced_state,
            )
            health_logger.log_health(EarlyFavoriteHealth(
                timestamp_utc="",
                received_at_ns=cur_ns,
                strategy_id=pos.strategy_id,
                match_id=match_id,
                market_name=pos.market_name,
                position_side=pos.side,
                position_token_id=pos.token_id,
                entry_ask=pos.entry_ask,
                entry_game_time_sec=pos.entry_game_time_sec,
                game_time_sec=game_time,
                health_state=state,
                reason=reason,
                book_favorite_side=book_favorite_side,
                networth_side=networth_side,
                model_side=model_side,
                current_bid=current_bid,
                opposite_ask=opposite_ask,
                opposite_fair=opposite_fair,
                opposite_edge=opposite_edge,
                lead=lead,
                book_age_ms=book_age_ms,
            ))
            if state == "SETTLED":
                self._closed.add(key)
                self._positions.pop(key, None)

    @staticmethod
    def _health_state(
        *,
        pos: _ShadowPosition,
        game_time: int,
        book_favorite_side: str,
        networth_side: str,
        model_side: str,
        opposite_fair: float | None,
        opposite_edge: float | None,
        forced_state: str | None,
    ) -> tuple[str, str]:
        if forced_state:
            return forced_state, "game_over"
        disagrees = [
            book_favorite_side and book_favorite_side != pos.side,
            networth_side and networth_side != pos.side,
            model_side and model_side != pos.side,
        ]
        disagree_count = sum(1 for x in disagrees if x)
        thesis_broken = disagree_count == 3 and game_time >= 720
        hedge_ok = (
            thesis_broken
            and opposite_fair is not None
            and opposite_edge is not None
            and opposite_fair > EARLY_FAVORITE_HEDGE_MIN_FAIR
            and opposite_edge >= EARLY_FAVORITE_HEDGE_MIN_EDGE
        )
        if hedge_ok:
            return "HEDGE_CANDIDATE", "book_networth_model_flipped_with_opposite_edge"
        if thesis_broken:
            return "THESIS_BROKEN", "book_networth_model_flipped"
        if disagree_count:
            return "WATCH", f"{disagree_count}_signal_disagreement"
        if game_time > EARLY_FAVORITE_LATE_NO_ENTRY_SEC:
            return "LATE_HOLD", "past_new_entry_window"
        return "HOLD", "thesis_intact"
