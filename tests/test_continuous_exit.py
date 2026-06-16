"""Tests for the continuous-strategy exit branch in live_exit_engine."""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from live_exit_engine import decide_live_exit, ExitDecision


@dataclass
class FakePosition:
    """Minimal stand-in for LivePosition. Carries the attributes the exit
    engine reads."""
    position_id: str = "p1"
    state: str = "open"
    token_id: str = "TOK"
    opposing_token_id: str = "TOK_OPP"
    match_id: str = "12345"
    market_name: str | None = "M1"
    side: str = "YES"
    entry_price: float = 0.50
    shares: float = 10.0
    cost_usd: float = 5.0
    entry_time_ns: int = 0
    entry_game_time_sec: int | None = 1800
    event_type: str = "CONTINUOUS"
    expected_move: float = 0.04
    fair_price: float = 0.55
    is_underdog_reversal: bool = False
    peak_bid: float = 0.0
    trader_kind: str = "continuous"
    exit_horizon_sec: int | None = 60
    signal_id: str | None = "sig-1"


# --- horizon timer ---
def test_holds_inside_horizon_window():
    pos = FakePosition(entry_time_ns=time.time_ns() - 30 * 1_000_000_000)
    book = {"best_bid": 0.50, "best_ask": 0.52}
    d = decide_live_exit(position=pos, book=book, game_over_match_ids=set())
    assert d.should_exit is False


def test_exits_at_horizon():
    pos = FakePosition(entry_time_ns=time.time_ns() - 61 * 1_000_000_000)
    book = {"best_bid": 0.50, "best_ask": 0.52}
    d = decide_live_exit(position=pos, book=book, game_over_match_ids=set())
    assert d.should_exit is True
    assert d.reason == "continuous_horizon"


def test_horizon_value_used_from_position():
    # Custom horizon (120s) — should NOT exit at 60s.
    pos = FakePosition(entry_time_ns=time.time_ns() - 90 * 1_000_000_000,
                       exit_horizon_sec=120)
    book = {"best_bid": 0.50, "best_ask": 0.52}
    d = decide_live_exit(position=pos, book=book, game_over_match_ids=set())
    assert d.should_exit is False


# --- adverse stop ---
def test_exits_on_adverse_stop():
    # 2026-05-29: min-hold default is 15s. Use 20s entry age to exercise the stop.
    pos = FakePosition(entry_time_ns=time.time_ns() - 20 * 1_000_000_000,
                       entry_price=0.50)
    book = {"best_bid": 0.45, "best_ask": 0.47}  # -5c from entry
    d = decide_live_exit(position=pos, book=book, game_over_match_ids=set())
    assert d.should_exit is True
    assert d.reason == "continuous_adverse_stop"


def test_does_not_exit_for_mild_adverse_move():
    pos = FakePosition(entry_time_ns=time.time_ns() - 20 * 1_000_000_000,
                       entry_price=0.50)
    book = {"best_bid": 0.48, "best_ask": 0.50}  # -2c, under 4c threshold
    d = decide_live_exit(position=pos, book=book, game_over_match_ids=set())
    assert d.should_exit is False


def test_adverse_stop_suppressed_during_min_hold_window():
    """Adverse stop must not fire within the first CONTINUOUS_ADVERSE_STOP_MIN_HOLD_SEC
    seconds. Prevents whipsaw losses on trades opened across a wide bid-ask spread."""
    # 5s into position — well inside the 15s default min-hold window.
    pos = FakePosition(entry_time_ns=time.time_ns() - 5 * 1_000_000_000,
                       entry_price=0.50)
    book = {"best_bid": 0.45, "best_ask": 0.47}  # -5c from entry — would trigger stop
    d = decide_live_exit(position=pos, book=book, game_over_match_ids=set())
    assert d.should_exit is False


# --- game over ---
def test_exits_on_game_over():
    pos = FakePosition(entry_time_ns=time.time_ns() - 5 * 1_000_000_000)
    book = {"best_bid": 0.50, "best_ask": 0.52}
    d = decide_live_exit(position=pos, book=book, game_over_match_ids={"12345"})
    assert d.should_exit is True
    assert d.reason == "game_over"


# --- trader_kind gating ---
def test_event_kind_falls_through_to_legacy_path():
    """A position tagged trader_kind='event' should NOT use the continuous
    branch — it should hit the existing event-detector logic."""
    pos = FakePosition(entry_time_ns=time.time_ns() - 30 * 1_000_000_000,
                       trader_kind="event", event_type="POLL_FIGHT_SWING")
    book = {"best_bid": 0.50, "best_ask": 0.52}
    d = decide_live_exit(position=pos, book=book, game_over_match_ids=set())
    # The legacy path runs full logic; mainly we just confirm we didn't get
    # the "continuous_horizon" reason at 30s into the position.
    assert "continuous" not in (d.reason or "")


def test_arb_immune_to_adverse_event():
    """Arb positions are hedged — exiting one leg on a directional adverse-event
    signal converts them into a one-sided bet. Arb must hold until game_over."""
    pos = FakePosition(entry_time_ns=time.time_ns() - 5 * 1_000_000_000,
                       trader_kind="arb")
    book = {"best_bid": 0.50, "best_ask": 0.52}
    d = decide_live_exit(position=pos, book=book,
                          game_over_match_ids=set(),
                          adverse_token_ids={"TOK"})
    assert d.should_exit is False, "arb must ignore adverse_event"


def test_dswing_holds_until_map_end():
    pos = FakePosition(entry_time_ns=time.time_ns() - 90 * 1_000_000_000,
                       trader_kind="dswing", event_type="DSWING")
    book = {"best_bid": 0.71, "best_ask": 0.73}
    d = decide_live_exit(position=pos, book=book, game_over_match_ids=set())
    assert d.should_exit is False


def test_dswing_exits_at_map_end_convergence():
    pos = FakePosition(entry_time_ns=time.time_ns() - 90 * 1_000_000_000,
                       trader_kind="dswing", event_type="DSWING")
    book = {"best_bid": 0.88, "best_ask": 0.90}
    d = decide_live_exit(position=pos, book=book, game_over_match_ids={"12345"})
    assert d.should_exit is True
    assert d.reason == "map_end_convergence"
    assert d.reference_bid == 0.88


def test_dswing_ignores_adverse_event_short_circuit():
    pos = FakePosition(entry_time_ns=time.time_ns() - 90 * 1_000_000_000,
                       trader_kind="dswing", event_type="DSWING")
    book = {"best_bid": 0.71, "best_ask": 0.73}
    d = decide_live_exit(position=pos, book=book,
                         game_over_match_ids=set(),
                         adverse_token_ids={"TOK"})
    assert d.should_exit is False


# --- adverse_token short-circuit still applies ---
def test_adverse_token_short_circuit_still_applies():
    pos = FakePosition(entry_time_ns=time.time_ns() - 5 * 1_000_000_000,
                       trader_kind="continuous")
    book = {"best_bid": 0.50, "best_ask": 0.52}
    d = decide_live_exit(position=pos, book=book,
                          game_over_match_ids=set(),
                          adverse_token_ids={"TOK"})
    assert d.should_exit is True
    assert d.reason == "adverse_event"
