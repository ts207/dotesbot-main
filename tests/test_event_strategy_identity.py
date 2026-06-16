from unittest.mock import MagicMock
import time

import pytest

import config
from live_exit_engine import decide_live_exit, ExitDecision
from live_position_store import LivePosition
from paper_trader import PaperTrader, Position


def test_live_position_metadata_assignment():
    # Verify metadata configuration matches based on is_reversal flag
    class FakeEvResult:
        is_reversal = True
        is_continuation = False
        actual_event_type = "MULTI_KILL_WINDOW"
        fair_price = 0.75
        signal_id = "sig_123"
        direction = "radiant"
        side = "YES"
        game_time_sec = 600
        lead = 1500
        derived_state_flags = ["SOME_FLAG"]

    ev_result = FakeEvResult()

    ev_strategy_kind = (
        "EVENT_REVERSAL_EDGE"
        if ev_result.is_reversal
        else "EVENT_CONTINUATION_EDGE"
    )
    ev_hold_policy = (
        "reversal_bounce_or_thesis"
        if ev_result.is_reversal
        else "thesis_invalidation"
    )
    ev_exit_engine = (
        "event_reversal_exit"
        if ev_result.is_reversal
        else "value_fair_invalidation"
    )

    pos = LivePosition(
        position_id="test",
        state="OPEN",
        token_id="YES",
        opposing_token_id="NO",
        match_id="m1",
        market_name="test_market",
        side=ev_result.side,
        entry_price=0.50,
        shares=100,
        cost_usd=50,
        entry_time_ns=time.time_ns(),
        entry_game_time_sec=ev_result.game_time_sec,
        event_type=ev_strategy_kind,
        expected_move=0.0,
        fair_price=ev_result.fair_price,
        trader_kind="value",
        exit_horizon_sec=None,
        signal_id=ev_result.signal_id,
        backed_direction=ev_result.direction,
        strategy_kind=ev_strategy_kind,
        strategy_family="EVENT",
        strategy_subtype=ev_result.actual_event_type,
        entry_is_reversal=ev_result.is_reversal,
        entry_is_continuation=ev_result.is_continuation,
        entry_engine="event_triggered_value",
        exit_engine=ev_exit_engine,
        hold_policy=ev_hold_policy,
        entry_fair=ev_result.fair_price,
        entry_edge=0.15,
        entry_backed_side=ev_result.direction,
        entry_radiant_lead=ev_result.lead,
        entry_actual_event_type=ev_result.actual_event_type,
        entry_derived_state_flags=list(ev_result.derived_state_flags),
    )

    assert pos.strategy_kind == "EVENT_REVERSAL_EDGE"
    assert pos.strategy_family == "EVENT"
    assert pos.hold_policy == "reversal_bounce_or_thesis"
    assert pos.exit_engine == "event_reversal_exit"
    assert pos.entry_is_reversal is True
    assert pos.entry_is_continuation is False


def test_live_exit_reversal_quarantine():
    pos = LivePosition(
        position_id="test_reversal",
        state="OPEN",
        token_id="YES",
        opposing_token_id="NO",
        match_id="m1",
        market_name="test_market",
        side="YES",
        entry_price=0.50,
        shares=100.0,
        cost_usd=50.0,
        entry_time_ns=time.time_ns() - 100 * 1_000_000_000,
        entry_game_time_sec=600,
        event_type="EVENT_REVERSAL_EDGE",
        expected_move=0.0,
        fair_price=0.80,
        trader_kind="value",
        strategy_kind="EVENT_REVERSAL_EDGE",
        hold_policy="reversal_bounce_or_thesis",
        entry_is_reversal=True,
    )

    book = {"best_bid": 0.65, "best_ask": 0.67}

    # Case A: Active exits disabled (default quarantine)
    config.EVENT_REVERSAL_ACTIVE_EXITS_ENABLED = False
    decision = decide_live_exit(
        position=pos,
        book=book,
        game_over_match_ids=set(),
        now_ns=time.time_ns(),
        game=None,
    )
    assert not decision.should_exit

    # Case B: Active exits enabled
    config.EVENT_REVERSAL_ACTIVE_EXITS_ENABLED = True
    decision = decide_live_exit(
        position=pos,
        book=book,
        game_over_match_ids=set(),
        now_ns=time.time_ns(),
        game=None,
    )
    assert decision.should_exit
    assert decision.reason == "event_reversal_bounce_take_profit"

    # Case C: game_over exit
    config.EVENT_REVERSAL_ACTIVE_EXITS_ENABLED = False
    decision = decide_live_exit(
        position=pos,
        book=book,
        game_over_match_ids={"m1"},
        now_ns=time.time_ns(),
        game=None,
    )
    assert decision.should_exit
    assert decision.reason == "game_over"

    # Case D: max_hold_timeout exit
    pos_old = LivePosition(
        position_id="test_reversal_old",
        state="OPEN",
        token_id="YES",
        opposing_token_id="NO",
        match_id="m1",
        market_name="test_market",
        side="YES",
        entry_price=0.50,
        shares=100.0,
        cost_usd=50.0,
        entry_time_ns=time.time_ns() - 30 * 3600 * 1_000_000_000,
        entry_game_time_sec=600,
        event_type="EVENT_REVERSAL_EDGE",
        expected_move=0.0,
        fair_price=0.80,
        trader_kind="value",
        strategy_kind="EVENT_REVERSAL_EDGE",
        hold_policy="reversal_bounce_or_thesis",
        entry_is_reversal=True,
    )
    decision = decide_live_exit(
        position=pos_old,
        book=book,
        game_over_match_ids=set(),
        now_ns=time.time_ns(),
        game=None,
    )
    assert decision.should_exit
    assert decision.reason == "max_hold_timeout"


def test_paper_exit_reversal_quarantine():
    pt = PaperTrader()
    config.EVENT_REVERSAL_ACTIVE_EXITS_ENABLED = False

    pos = Position(
        token_id="YES",
        match_id="m1",
        market_name="test_market",
        side="YES",
        entry_price=0.50,
        shares=100.0,
        cost_usd=50.0,
        entry_time_ns=time.time_ns() - 100 * 1_000_000_000,
        entry_game_time_sec=600,
        event_type="EVENT_REVERSAL_EDGE",
        lag=0.0,
        expected_move=0.0,
        fair_price=0.80,
        strategy_kind="EVENT_REVERSAL_EDGE",
        hold_policy="reversal_bounce_or_thesis",
        entry_is_reversal=True,
    )
    pt.positions["YES"] = pos

    book_store = {"YES": {"best_bid": 0.65, "best_ask": 0.67}}

    # Should not exit on take profit when disabled
    closed = pt.check_exits(book_store=book_store, game_over_match_ids=set())
    assert len(closed) == 0

    # Enable active exits
    config.EVENT_REVERSAL_ACTIVE_EXITS_ENABLED = True
    closed = pt.check_exits(book_store=book_store, game_over_match_ids=set())
    assert len(closed) == 1
    assert closed[0].exit_reason == "event_reversal_bounce_take_profit"
