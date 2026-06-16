from __future__ import annotations

import time

from exit_policy import ExitPolicy
from live_exit_engine import decide_live_exit
from live_position_store import LivePosition
from paper_trader import Position


def test_dswing_exit_policy_matches_live_decision():
    now = time.time_ns()
    live_pos = LivePosition(
        position_id="p",
        state="OPEN",
        token_id="YES",
        opposing_token_id="NO",
        match_id="M1",
        market_name="M",
        side="YES",
        entry_price=0.5,
        shares=10,
        cost_usd=5,
        entry_time_ns=now - 60_000_000_000,
        entry_game_time_sec=600,
        event_type="DSWING",
        expected_move=0,
        fair_price=0.7,
        trader_kind="dswing",
        strategy_kind="DSWING",
        hold_policy="map_end_convergence",
    )
    paper_pos = Position(
        token_id="YES",
        match_id="M1",
        market_name="M",
        side="YES",
        entry_price=0.5,
        shares=10,
        cost_usd=5,
        entry_time_ns=live_pos.entry_time_ns,
        entry_game_time_sec=600,
        event_type="DSWING",
        lag=0,
        expected_move=0,
        fair_price=0.7,
        strategy_kind="DSWING",
        hold_policy="map_end_convergence",
    )
    book = {"best_bid": 0.62, "best_ask": 0.64}

    live_decision = decide_live_exit(
        position=live_pos,
        book=book,
        game_over_match_ids={"M1"},
        now_ns=now,
    )
    paper_decision = ExitPolicy.decide(
        paper_pos,
        book,
        None,
        {"M1"},
        now_ns=now,
        catastrophe_floor=0.0,
    )

    assert live_decision.should_exit is True
    assert paper_decision.should_exit is True
    assert live_decision.reason == paper_decision.reason == "map_end_convergence"
    assert live_decision.reference_bid == paper_decision.reference_bid == 0.62


def test_event_reversal_policy_holds_when_quarantined(monkeypatch):
    monkeypatch.setattr("config.EVENT_REVERSAL_ACTIVE_EXITS_ENABLED", False)
    pos = Position(
        token_id="YES",
        match_id="M1",
        market_name="M",
        side="YES",
        entry_price=0.5,
        shares=10,
        cost_usd=5,
        entry_time_ns=time.time_ns() - 90_000_000_000,
        entry_game_time_sec=600,
        event_type="EVENT_REVERSAL_EDGE",
        lag=0,
        expected_move=0,
        fair_price=0.8,
        strategy_kind="EVENT_REVERSAL_EDGE",
        hold_policy="reversal_bounce_or_thesis",
        entry_is_reversal=True,
    )

    decision = ExitPolicy.decide(
        pos,
        {"best_bid": 0.70, "best_ask": 0.72},
        None,
        set(),
        catastrophe_floor=0.0,
    )

    assert decision.should_exit is False
