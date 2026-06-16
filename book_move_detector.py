from __future__ import annotations

import time
from collections import deque
from datetime import datetime, timezone

from config import BOOK_MOVE_WINDOW_SEC, BOOK_MOVE_THRESHOLD, BOOK_MOVE_DEBOUNCE_SEC


def _mid(book: dict) -> float | None:
    bid = book.get("best_bid")
    ask = book.get("best_ask")
    try:
        if bid is not None and ask is not None:
            return (float(bid) + float(ask)) / 2
        if ask is not None:
            return float(ask)
        if bid is not None:
            return float(bid)
    except (TypeError, ValueError):
        pass
    return None


def _book_age_ms(book: dict) -> int | None:
    ns = book.get("received_at_ns")
    if ns:
        return int((time.time_ns() - ns) / 1_000_000)
    return None


class BookMoveDetector:
    """Detects rapid mid-price moves in the Polymarket book.

    On each on_book_update() call, records the current mid and checks whether
    the price has moved >= threshold over the last window_sec seconds. Fires at
    most once per direction per debounce_sec window per token.
    """

    def __init__(
        self,
        *,
        window_sec: float = BOOK_MOVE_WINDOW_SEC,
        threshold: float = BOOK_MOVE_THRESHOLD,
        debounce_sec: float = BOOK_MOVE_DEBOUNCE_SEC,
        max_history: int = 500,
    ):
        self.window_sec = window_sec
        self.threshold = threshold
        self.debounce_sec = debounce_sec
        # token_id -> deque of (wall_time_float, mid_float)
        self._history: dict[str, deque] = {}
        # (token_id, direction) -> last_fire_time
        self._last_fire: dict[tuple, float] = {}

    def record_mid(self, token_id: str, mid: float, ts: float | None = None) -> None:
        if token_id not in self._history:
            self._history[token_id] = deque(maxlen=500)
        self._history[token_id].append((ts or time.time(), mid))

    def check(self, token_id: str) -> dict | None:
        history = self._history.get(token_id)
        if not history or len(history) < 2:
            return None

        now = time.time()
        current_mid = history[-1][1]

        # Find the oldest entry within window_sec
        anchor_time = None
        anchor_mid = None
        actual_window = 0.0
        for t, m in history:
            if now - t <= self.window_sec:
                if anchor_time is None:
                    anchor_time = t
                    anchor_mid = m
                    actual_window = now - t

        if anchor_mid is None:
            return None

        magnitude = current_mid - anchor_mid

        # Require at least 0.5s of price history to avoid single-tick noise
        if actual_window < 0.5:
            return None

        # Scale threshold down proportionally if actual window < configured window
        effective_threshold = self.threshold * (actual_window / self.window_sec) if actual_window < self.window_sec else self.threshold

        if abs(magnitude) < effective_threshold:
            return None

        direction = "up" if magnitude > 0 else "down"
        debounce_key = (token_id, direction)
        last = self._last_fire.get(debounce_key, 0.0)
        if now - last < self.debounce_sec:
            return None

        self._last_fire[debounce_key] = now

        return {
            "signal_type": "book_move",
            "token_id": token_id,
            "direction": direction,
            "magnitude": round(magnitude, 4),
            "current_mid": round(current_mid, 4),
            "anchor_mid": round(anchor_mid, 4),
            "window_sec": round(actual_window, 1),
            "timestamp": now,
            "timestamp_utc": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(timespec="milliseconds"),
        }

    def on_book_update(
        self,
        token_id: str,
        book: dict,
        steam_games: dict | None = None,
        mappings: list | None = None,
    ) -> dict | None:
        mid = _mid(book)
        if mid is None:
            return None

        self.record_mid(token_id, mid)
        sig = self.check(token_id)
        if sig is None:
            return None

        # Annotate with book state
        bid = book.get("best_bid")
        ask = book.get("best_ask")
        sig["best_bid"] = float(bid) if bid is not None else None
        sig["best_ask"] = float(ask) if ask is not None else None
        sig["spread"] = round(float(ask) - float(bid), 4) if bid is not None and ask is not None else None
        sig["book_age_ms"] = _book_age_ms(book)

        # Annotate with Steam/mapping context
        if mappings and steam_games:
            for m in mappings:
                if token_id in (m.get("yes_token_id"), m.get("no_token_id")):
                    sig["match_id"] = m.get("dota_match_id")
                    sig["market_name"] = m.get("name")
                    game = steam_games.get(str(m.get("dota_match_id") or ""))
                    if game:
                        sig["game_time_sec"] = game.get("game_time_sec")
                        sig["radiant_lead"] = game.get("radiant_lead")
                        received_ns = game.get("received_at_ns")
                        if received_ns:
                            sig["steam_age_ms"] = int((time.time_ns() - received_ns) / 1_000_000)
                    break

        return sig
