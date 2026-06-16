"""Tests for EVENT_REVERSAL_EDGE live exit quarantine/config."""
from __future__ import annotations

from types import SimpleNamespace

import config
from live_exit_engine import decide_live_exit


ENTRY_NS = 1_000_000_000_000


def _pos(**overrides):
    base = {
        "token_id": "tok_A",
        "match_id": "m1",
        "trader_kind": "value",
        "strategy_kind": "EVENT_REVERSAL_EDGE",
        "hold_policy": "reversal_bounce_or_thesis",
        "entry_time_ns": ENTRY_NS,
        "entry_price": 0.40,
        "fair_price": 0.50,
        "expected_move": 0.10,
        "backed_direction": "radiant",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_event_reversal_active_exits_disabled_holds_even_at_bounce(monkeypatch):
    monkeypatch.setattr(config, "EVENT_REVERSAL_ACTIVE_EXITS_ENABLED", False, raising=False)
    monkeypatch.setattr(config, "EVENT_REVERSAL_TAKE_PROFIT_CENTS", 0.08, raising=False)
    monkeypatch.setattr(config, "EVENT_REVERSAL_MAX_HOLD_SEC", 60, raising=False)

    decision = decide_live_exit(
        position=_pos(),
        book={"best_bid": 0.60},
        game_over_match_ids=set(),
        now_ns=ENTRY_NS + 30_000_000_000,
    )

    assert decision.should_exit is False
    assert decision.reason == ""


def test_event_reversal_active_exits_enabled_takes_bounce_profit(monkeypatch):
    monkeypatch.setattr(config, "EVENT_REVERSAL_ACTIVE_EXITS_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "EVENT_REVERSAL_TAKE_PROFIT_CENTS", 0.08, raising=False)
    monkeypatch.setattr(config, "EVENT_REVERSAL_MAX_HOLD_SEC", 300, raising=False)

    decision = decide_live_exit(
        position=_pos(),
        book={"best_bid": 0.49},
        game_over_match_ids=set(),
        now_ns=ENTRY_NS + 30_000_000_000,
    )

    assert decision.should_exit is True
    assert decision.reason == "event_reversal_bounce_take_profit"
    assert decision.reference_bid == 0.49


def test_event_reversal_active_exits_enabled_times_out(monkeypatch):
    monkeypatch.setattr(config, "EVENT_REVERSAL_ACTIVE_EXITS_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "EVENT_REVERSAL_TAKE_PROFIT_CENTS", 0.08, raising=False)
    monkeypatch.setattr(config, "EVENT_REVERSAL_MAX_HOLD_SEC", 60, raising=False)

    decision = decide_live_exit(
        position=_pos(),
        book={"best_bid": 0.42},
        game_over_match_ids=set(),
        now_ns=ENTRY_NS + 61_000_000_000,
    )

    assert decision.should_exit is True
    assert decision.reason == "event_reversal_timeout"
    assert decision.reference_bid == 0.42


def test_event_reversal_still_exits_on_game_over_when_active_exits_disabled(monkeypatch):
    monkeypatch.setattr(config, "EVENT_REVERSAL_ACTIVE_EXITS_ENABLED", False, raising=False)

    decision = decide_live_exit(
        position=_pos(),
        book={"best_bid": 0.41},
        game_over_match_ids={"m1"},
        now_ns=ENTRY_NS + 30_000_000_000,
    )

    assert decision.should_exit is True
    assert decision.reason == "game_over"
    assert decision.reference_bid == 0.41
