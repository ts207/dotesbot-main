"""Stateful wrapper around `continuous_scorer.score_snapshot`.

Holds per-match snapshot history (last 4 top_live snapshots) and per-match
pregame anchor (first observed YES mid). Pure pull model: callers invoke
`observe(game)` on each incoming snapshot; the engine fetches current book
state from a passed-in `BookStore` and returns a list of `ContinuousSignal`
or `ScoreReject` instances.

Thread-safety: single-threaded by design. The bot's snapshot loop is
single-writer; wrap with your own lock if multiple producers feed it.
"""
from __future__ import annotations

import os
from collections import defaultdict, deque
from typing import Any, Mapping, Sequence

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from continuous_scorer import (
    score_snapshot,
    ContinuousSignal,
    ScoreReject,
)

# Feature flag — when false, main.py skips instantiation entirely.
CONTINUOUS_ENGINE_ENABLED = os.getenv("CONTINUOUS_ENGINE_ENABLED", "false").lower() in {"1", "true", "yes"}

# When true AND CONTINUOUS_ENGINE_ENABLED is true AND ENABLE_REAL_LIVE_TRADING
# is true, continuous signals submit FAK buys via LiveExecutor.try_buy_continuous.
# When false, signals are logged to logs/continuous_attempts.csv but never sent.
ENABLE_CONTINUOUS_TRADING = os.getenv("ENABLE_CONTINUOUS_TRADING", "false").lower() in {"1", "true", "yes"}

# History depth: just need the previous snapshot + current. Keep a few extra
# for future 3-snap features without re-wiring.
HISTORY_DEPTH = int(os.getenv("CONTINUOUS_HISTORY_DEPTH", "4"))

# If the bot starts mid-match, we don't have a true pregame anchor. Fall back
# to 0.5 (neutral) so the conviction multiplier doesn't fire spuriously.
DEFAULT_PREGAME_MID = float(os.getenv("CONTINUOUS_DEFAULT_PREGAME_MID", "0.5"))


def _book_with_mid(book: Mapping | None) -> dict | None:
    """The `BookStore` from `poly_ws.py` stores `best_bid`/`best_ask` but no
    explicit `mid` field. The scorer expects `mid`; derive it here."""
    if book is None:
        return None
    bid = book.get("best_bid")
    ask = book.get("best_ask")
    if bid is None or ask is None:
        return None
    return {
        "best_bid": bid,
        "best_ask": ask,
        "mid": (bid + ask) / 2.0,
        "bid_size": book.get("bid_size"),
        "ask_size": book.get("ask_size"),
        "received_at_ns": book.get("received_at_ns"),
    }


class ContinuousEngine:
    """Stateful driver for the continuous scorer.

    Usage:
        engine = ContinuousEngine(mappings=[...])
        for game in snapshot_stream:
            results = engine.observe(game, book_store)
            for r in results:
                if isinstance(r, ContinuousSignal):
                    executor.try_buy_continuous(r)
                else:
                    shadow_logger.log_reject(r)
    """

    def __init__(self, mappings: Sequence[Mapping] | Mapping[str, Mapping]):
        # mappings may be a list (from markets.yaml) or a dict keyed by
        # dota_match_id. Normalize to the dict form for O(1) lookup.
        self._mappings: dict[str, dict] = {}
        if isinstance(mappings, Mapping):
            for k, v in mappings.items():
                self._mappings[str(k)] = dict(v)
        else:
            for m in mappings:
                mid = m.get("dota_match_id")
                if mid and str(mid).isdigit() and str(mid) != "123":
                    self._mappings[str(mid)] = dict(m)

        self._history: dict[str, deque] = defaultdict(lambda: deque(maxlen=HISTORY_DEPTH))
        self._pregame_anchors: dict[str, float] = {}
        # Stats for operational debugging (not load-bearing).
        self._observed_count = 0
        self._signal_count = 0
        self._reject_counts: dict[str, int] = defaultdict(int)

    # ------- public API -------
    def observe(self, game: Mapping, book_store: Any) -> list[ContinuousSignal | ScoreReject]:
        """Process one snapshot. Returns 0 or more results.

        Returns an empty list when:
          - the match isn't mapped to a Polymarket market
          - we don't have a previous snapshot yet (first observation of a match)
          - data_source isn't 'top_live' (signals run on the fast path only)

        Returns a list of 1 result (signal or reject) on a successful scoring.
        """
        self._observed_count += 1

        match_id = str(game.get("match_id") or "")
        if not match_id:
            return []

        # Only the top_live path is signal-eligible; live_league snapshots
        # are slower and previously caused noisy book joins. Keep the same
        # convention as the legacy signal_engine.
        if game.get("data_source") != "top_live":
            return []

        mapping = self._mappings.get(match_id)
        if mapping is None:
            self._reject_counts["no_mapping_for_match"] += 1
            return [ScoreReject(match_id, int(game.get("received_at_ns") or 0),
                                "no_mapping_for_match", {})]

        yes_token = mapping.get("yes_token_id")
        no_token = mapping.get("no_token_id")
        if not yes_token or not no_token:
            self._reject_counts["mapping_missing_tokens"] += 1
            return [ScoreReject(match_id, int(game.get("received_at_ns") or 0),
                                "mapping_missing_tokens", {})]

        # Refresh pregame anchor on first YES book tick for this match.
        if match_id not in self._pregame_anchors:
            raw_yes = book_store.get(yes_token) if book_store else None
            ywm = _book_with_mid(raw_yes)
            if ywm is not None:
                self._pregame_anchors[match_id] = ywm["mid"]

        # Add the snapshot to history. Need at least 2 to score.
        self._history[match_id].append(dict(game))
        history = list(self._history[match_id])
        if len(history) < 2:
            return []

        prev_snap = history[-2]
        cur_snap = history[-1]

        yes_book = _book_with_mid(book_store.get(yes_token) if book_store else None)
        no_book = _book_with_mid(book_store.get(no_token) if book_store else None)
        if yes_book is None or no_book is None:
            self._reject_counts["book_unavailable"] += 1
            return [ScoreReject(match_id, int(cur_snap.get("received_at_ns") or 0),
                                "book_unavailable", {})]

        pregame = self._pregame_anchors.get(match_id, DEFAULT_PREGAME_MID)

        result = score_snapshot(
            prev_snap=prev_snap,
            cur_snap=cur_snap,
            yes_book=yes_book,
            no_book=no_book,
            pregame_yes_mid=pregame,
            mapping=mapping,
        )

        if isinstance(result, ContinuousSignal):
            self._signal_count += 1
        else:
            self._reject_counts[result.reason] += 1

        return [result]

    # ------- introspection -------
    def stats(self) -> dict:
        return {
            "observed": self._observed_count,
            "signals_emitted": self._signal_count,
            "rejects_by_reason": dict(self._reject_counts),
            "matches_tracked": len(self._history),
            "pregame_anchors_set": len(self._pregame_anchors),
        }

    def refresh_mappings(self, mappings: Sequence[Mapping] | Mapping[str, Mapping]) -> None:
        """Reload mappings (used when sync_markets refreshes markets.yaml).
        Existing snapshot history and pregame anchors are preserved."""
        new: dict[str, dict] = {}
        if isinstance(mappings, Mapping):
            for k, v in mappings.items():
                new[str(k)] = dict(v)
        else:
            for m in mappings:
                mid = m.get("dota_match_id")
                if mid and str(mid).isdigit() and str(mid) != "123":
                    new[str(mid)] = dict(m)
        self._mappings = new

    def forget_match(self, match_id: str) -> None:
        """Drop a match's history (e.g. when game_over fires). Pregame
        anchor is also dropped so a future re-observation re-anchors."""
        self._history.pop(match_id, None)
        self._pregame_anchors.pop(match_id, None)
