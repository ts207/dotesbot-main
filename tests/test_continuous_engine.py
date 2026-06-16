"""Tests for ContinuousEngine — state management and book wiring."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from continuous_engine import ContinuousEngine, _book_with_mid
from continuous_scorer import ContinuousSignal, ScoreReject


class FakeBookStore:
    """Minimal BookStore stand-in: dict of asset_id → book dict."""
    def __init__(self, books: dict[str, dict] | None = None):
        self._books = books or {}

    def get(self, asset_id: str):
        return self._books.get(asset_id)

    def set(self, asset_id: str, *, bid, ask, bid_size=100, ask_size=100):
        self._books[asset_id] = {"best_bid": bid, "best_ask": ask,
                                  "bid_size": bid_size, "ask_size": ask_size,
                                  "received_at_ns": time.time_ns()}


def _mapping(match_id="12345", yes="YES_TOK", no="NO_TOK", side="normal"):
    return {"dota_match_id": match_id, "yes_token_id": yes, "no_token_id": no,
            "steam_radiant_team": "A", "steam_dire_team": "B",
            "steam_side_mapping": side, "yes_team": "A", "name": "M1"}


def _game(match_id="12345", *, ns, gt, rl, rs, ds, data_source="top_live"):
    return {"match_id": match_id, "received_at_ns": ns,
            "game_time_sec": gt, "radiant_lead": rl,
            "radiant_score": rs, "dire_score": ds,
            "data_source": data_source}


# --- helper ---
def test_book_with_mid_derives_mid_from_bid_ask():
    raw = {"best_bid": 0.54, "best_ask": 0.56, "bid_size": 100, "ask_size": 100,
           "received_at_ns": 1}
    out = _book_with_mid(raw)
    assert out["mid"] == pytest.approx(0.55)


def test_book_with_mid_handles_missing_inputs():
    assert _book_with_mid(None) is None
    assert _book_with_mid({"best_bid": 0.5, "best_ask": None}) is None
    assert _book_with_mid({"best_bid": None, "best_ask": 0.5}) is None


# --- first observation ---
def test_first_observation_returns_empty_list():
    engine = ContinuousEngine([_mapping()])
    store = FakeBookStore()
    store.set("YES_TOK", bid=0.54, ask=0.56)
    store.set("NO_TOK",  bid=0.44, ask=0.46)
    result = engine.observe(_game(ns=1_000_000_000, gt=1800, rl=500, rs=10, ds=10), store)
    # First snapshot — no previous to delta against.
    assert result == []


def test_second_observation_fires_signal_when_gates_pass():
    engine = ContinuousEngine([_mapping()])
    store = FakeBookStore()
    store.set("YES_TOK", bid=0.54, ask=0.56)
    store.set("NO_TOK",  bid=0.44, ask=0.46)
    engine.observe(_game(ns=1_000_000_000_000_000_000, gt=1800,
                         rl=500, rs=10, ds=10), store)
    result = engine.observe(_game(ns=1_000_000_020_000_000_000, gt=1820,
                                  rl=2500, rs=12, ds=10), store)
    assert len(result) == 1
    assert isinstance(result[0], ContinuousSignal)
    assert result[0].direction == 1
    assert result[0].side == "YES"


def test_non_top_live_snapshots_are_ignored():
    engine = ContinuousEngine([_mapping()])
    store = FakeBookStore()
    store.set("YES_TOK", bid=0.54, ask=0.56)
    store.set("NO_TOK",  bid=0.44, ask=0.46)
    result = engine.observe(_game(ns=1, gt=1800, rl=500, rs=10, ds=10,
                                   data_source="live_league"), store)
    assert result == []


def test_missing_mapping_returns_reject():
    engine = ContinuousEngine([])  # no mappings at all
    store = FakeBookStore()
    result = engine.observe(_game(ns=1, gt=1800, rl=500, rs=10, ds=10), store)
    assert len(result) == 1
    assert isinstance(result[0], ScoreReject)
    assert result[0].reason == "no_mapping_for_match"


def test_missing_book_returns_reject_after_history_warm():
    engine = ContinuousEngine([_mapping()])
    store = FakeBookStore()  # no books at all
    # First observation seeds history but returns empty (still need 2)
    engine.observe(_game(ns=1, gt=1800, rl=500, rs=10, ds=10), store)
    result = engine.observe(_game(ns=2, gt=1820, rl=2500, rs=12, ds=10), store)
    assert len(result) == 1
    assert isinstance(result[0], ScoreReject)
    assert result[0].reason == "book_unavailable"


# --- pregame anchor behavior ---
def test_pregame_anchor_captured_on_first_book_observation():
    engine = ContinuousEngine([_mapping()])
    store = FakeBookStore()
    store.set("YES_TOK", bid=0.60, ask=0.62)  # pregame anchor will be 0.61
    store.set("NO_TOK",  bid=0.38, ask=0.40)
    engine.observe(_game(ns=1, gt=1800, rl=500, rs=10, ds=10), store)
    assert engine._pregame_anchors["12345"] == pytest.approx(0.61)

    # Even after the YES mid moves later, the pregame anchor stays.
    store.set("YES_TOK", bid=0.74, ask=0.76)
    engine.observe(_game(ns=2, gt=1820, rl=2500, rs=12, ds=10), store)
    assert engine._pregame_anchors["12345"] == pytest.approx(0.61)


def test_pregame_anchor_uses_default_when_no_book_at_first_observation():
    engine = ContinuousEngine([_mapping()])
    store = FakeBookStore()  # no YES book yet
    engine.observe(_game(ns=1, gt=1800, rl=500, rs=10, ds=10), store)
    # Anchor was not set because YES book was missing
    assert "12345" not in engine._pregame_anchors

    # Now the book appears — anchor gets captured on the next observation
    store.set("YES_TOK", bid=0.54, ask=0.56)
    store.set("NO_TOK",  bid=0.44, ask=0.46)
    engine.observe(_game(ns=2, gt=1820, rl=2500, rs=12, ds=10), store)
    assert engine._pregame_anchors["12345"] == pytest.approx(0.55)


# --- history depth and forget ---
def test_history_depth_is_bounded():
    engine = ContinuousEngine([_mapping()])
    store = FakeBookStore()
    store.set("YES_TOK", bid=0.54, ask=0.56)
    store.set("NO_TOK",  bid=0.44, ask=0.46)
    for i in range(10):
        engine.observe(_game(ns=i, gt=1800 + i, rl=500, rs=10, ds=10), store)
    # Should not have grown to 10 — capped by HISTORY_DEPTH (default 4).
    assert len(engine._history["12345"]) <= 4


def test_forget_match_clears_state():
    engine = ContinuousEngine([_mapping()])
    store = FakeBookStore()
    store.set("YES_TOK", bid=0.54, ask=0.56)
    store.set("NO_TOK",  bid=0.44, ask=0.46)
    engine.observe(_game(ns=1, gt=1800, rl=500, rs=10, ds=10), store)
    assert "12345" in engine._history
    assert "12345" in engine._pregame_anchors
    engine.forget_match("12345")
    assert "12345" not in engine._history
    assert "12345" not in engine._pregame_anchors


# --- mapping refresh ---
def test_refresh_mappings_replaces_lookup_without_losing_history():
    engine = ContinuousEngine([_mapping()])
    store = FakeBookStore()
    store.set("YES_TOK", bid=0.54, ask=0.56)
    store.set("NO_TOK",  bid=0.44, ask=0.46)
    engine.observe(_game(ns=1, gt=1800, rl=500, rs=10, ds=10), store)

    # Refresh with a new mapping for a different match. M1 should retain
    # its history but the lookup for M1 is now gone (so the next observe
    # for M1 would be "no_mapping_for_match").
    engine.refresh_mappings([_mapping(match_id="M2", yes="YES2", no="NO2")])
    assert "12345" in engine._history    # history preserved
    assert "M2" not in engine._history  # but no history for M2 yet


# --- stats ---
def test_stats_track_counts():
    engine = ContinuousEngine([_mapping()])
    store = FakeBookStore()
    store.set("YES_TOK", bid=0.54, ask=0.56)
    store.set("NO_TOK",  bid=0.44, ask=0.46)
    engine.observe(_game(ns=1_000_000_000_000_000_000, gt=1800,
                         rl=500, rs=10, ds=10), store)
    engine.observe(_game(ns=1_000_000_020_000_000_000, gt=1820,
                         rl=2500, rs=12, ds=10), store)
    stats = engine.stats()
    assert stats["observed"] == 2
    assert stats["signals_emitted"] == 1
    assert stats["matches_tracked"] == 1
    assert stats["pregame_anchors_set"] == 1


# --- reversed side mapping wired through ---
def test_reversed_side_mapping_flips_direction():
    engine = ContinuousEngine([_mapping(side="reversed")])
    store = FakeBookStore()
    # When reversed, YES==dire. So a positive radiant_lead means dire (YES) is BEHIND,
    # and a positive d_lead favors NO direction.
    store.set("YES_TOK", bid=0.44, ask=0.46)   # YES (dire) is the underdog
    store.set("NO_TOK",  bid=0.54, ask=0.56)
    engine.observe(_game(ns=1_000_000_000_000_000_000, gt=1800,
                         rl=500, rs=10, ds=10), store)
    result = engine.observe(_game(ns=1_000_000_020_000_000_000, gt=1820,
                                  rl=2500, rs=12, ds=10), store)
    assert len(result) == 1
    sig = result[0]
    assert isinstance(sig, ContinuousSignal)
    assert sig.direction == -1   # radiant gained → YES (dire) lost ground → favor NO
    assert sig.side == "NO"
