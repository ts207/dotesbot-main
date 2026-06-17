"""Tests for strategy_execution.py."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from dataclasses import dataclass

from strategy_execution import (
    StrategyExecutionContext,
    StrategyExecutionLoggers,
    execute_allocation_decisions,
)
from strategy_allocator import AllocationDecision, StrategyCandidate
from value_engine import ValueSignal
from decisive_swing_engine import DSwingSignal
from event_triggered_value_engine import EventTriggeredValueSignal


@pytest.fixture
def base_ctx():
    return StrategyExecutionContext(
        game={"match_id": "m1"},
        mapping={"yes_token_id": "tok_yes", "no_token_id": "tok_no", "name": "M1"},
        book_store=MagicMock(),
        trader=MagicMock(),
        live_executor=AsyncMock(),
        live_position_store=MagicMock(),
        entered_tokens=set(),
        loggers=StrategyExecutionLoggers(
            live_logger=MagicMock(),
            position_logger=MagicMock(),
        ),
        normalized_entry_fill_fn=MagicMock(return_value=(10.0, 100.0, 0.10)),
        annotate_signal_policy_for_paper_fn=lambda **kwargs: kwargs["signal"],
        enable_real_live_trading=False,
    )


@pytest.mark.asyncio
async def test_no_winner_is_skipped(base_ctx):
    dec = AllocationDecision(token_id="tok_yes", match_id="m1", winner=None)
    results = await execute_allocation_decisions([dec], base_ctx)
    assert len(results) == 0
    base_ctx.live_executor.try_buy_value.assert_not_called()


@pytest.mark.asyncio
async def test_value_winner_uses_live_path(base_ctx):
    sig = MagicMock(spec=ValueSignal)
    sig.token_id = "tok_yes"
    sig.side = "yes"
    sig.fair_price = 0.50
    sig.ask = 0.48
    sig.edge = 0.02
    sig.lead = 1000
    sig.direction = "radiant"
    sig.game_time_sec = 900
    sig.edge_type = "value"
    sig.target_horizon = "30s"
    sig.expected_hold_sec = 30
    sig.entry_trigger = "e"
    sig.exit_trigger = "x"
    sig.primary_metric = "p"
    sig.secondary_metric = "s"
    sig.promotion_rule = "pr"
    sig.disable_rule = "dr"
    sig.signal_id = "sig1"

    cand = StrategyCandidate(
        strategy="VALUE_EDGE", token_id="tok_yes", match_id="m1", 
        direction="radiant", edge=0.02, fair=0.50, game_time_sec=900, 
        signal=sig, would_pass_confirmation=True
    )
    dec = AllocationDecision(token_id="tok_yes", match_id="m1", winner=cand)
    
    attempt = MagicMock()
    attempt.filled_size_usd = 10.0
    attempt.order_status = "filled"
    attempt.avg_fill_price = 0.48
    attempt.match_id = "m1"
    attempt.token_id = "tok_yes"
    attempt.created_at_ns = 12345
    attempt.order_id = "order1"
    base_ctx.live_executor.try_buy_value.return_value = attempt
    
    results = await execute_allocation_decisions([dec], base_ctx)
    
    assert len(results) == 1
    assert results[0].status == "entered"
    assert results[0].mode == "LIVE"
    base_ctx.live_executor.try_buy_value.assert_called_once()
    base_ctx.live_position_store.add.assert_called_once()
    assert "tok_yes" in base_ctx.entered_tokens


@pytest.mark.asyncio
async def test_value_winner_paper_path(base_ctx):
    base_ctx.live_executor = None
    
    sig = MagicMock(spec=ValueSignal)
    sig.token_id = "tok_yes"
    sig.side = "yes"
    sig.edge = 0.02
    sig.to_signal_dict.return_value = {"sig": "data"}
    
    cand = StrategyCandidate(
        strategy="VALUE_EDGE", token_id="tok_yes", match_id="m1", 
        direction="radiant", edge=0.02, fair=0.50, game_time_sec=900, 
        signal=sig, would_pass_confirmation=True
    )
    dec = AllocationDecision(token_id="tok_yes", match_id="m1", winner=cand)
    
    pos = MagicMock()
    pos.position_id = "p1"
    pos.entry_price = 0.48
    base_ctx.trader.enter.return_value = (pos, "ok")
    
    results = await execute_allocation_decisions([dec], base_ctx)
    
    assert len(results) == 1
    assert results[0].status == "entered"
    assert results[0].mode == "PAPER"
    base_ctx.trader.enter.assert_called_once()
    base_ctx.loggers.position_logger.log_entry.assert_called_once_with(pos)


@pytest.mark.asyncio
async def test_rejected_live_attempt_does_not_create_position(base_ctx):
    sig = MagicMock(spec=ValueSignal)
    sig.token_id = "tok_yes"
    sig.side = "yes"
    
    cand = StrategyCandidate(
        strategy="VALUE_EDGE", token_id="tok_yes", match_id="m1", 
        direction="radiant", edge=0.02, fair=0.50, game_time_sec=900, 
        signal=sig, would_pass_confirmation=True
    )
    dec = AllocationDecision(token_id="tok_yes", match_id="m1", winner=cand)
    
    attempt = MagicMock()
    attempt.filled_size_usd = 0.0
    attempt.order_status = "rejected"
    attempt.reason_if_rejected = "price_moved"
    base_ctx.live_executor.try_buy_value.return_value = attempt
    
    results = await execute_allocation_decisions([dec], base_ctx)
    
    assert len(results) == 1
    assert results[0].status == "rejected"
    base_ctx.live_position_store.add.assert_not_called()


@pytest.mark.asyncio
async def test_delayed_live_attempt_creates_pending_entry_position(base_ctx):
    sig = MagicMock(spec=ValueSignal)
    sig.token_id = "tok_yes"
    sig.side = "yes"
    sig.fair_price = 0.50
    sig.ask = 0.48
    sig.lead = 1000
    sig.direction = "radiant"
    sig.game_time_sec = 900
    sig.edge_type = "value"
    sig.target_horizon = "30s"
    sig.expected_hold_sec = 30
    sig.entry_trigger = "e"
    sig.exit_trigger = "x"
    sig.primary_metric = "p"
    sig.secondary_metric = "s"
    sig.promotion_rule = "pr"
    sig.disable_rule = "dr"
    sig.signal_id = "sig1"
    sig.edge = 0.02

    cand = StrategyCandidate(
        strategy="VALUE_EDGE", token_id="tok_yes", match_id="m1", 
        direction="radiant", edge=0.02, fair=0.50, game_time_sec=900, 
        signal=sig, would_pass_confirmation=True
    )
    dec = AllocationDecision(token_id="tok_yes", match_id="m1", winner=cand)
    
    attempt = MagicMock()
    attempt.filled_size_usd = 0.0
    attempt.order_status = "delayed"
    attempt.match_id = "m1"
    attempt.token_id = "tok_yes"
    attempt.created_at_ns = 12345
    attempt.order_id = "order1"
    base_ctx.live_executor.try_buy_value.return_value = attempt
    
    results = await execute_allocation_decisions([dec], base_ctx)
    
    assert len(results) == 1
    assert results[0].status == "entered"
    
    # Verify the position state
    args, _ = base_ctx.live_position_store.add.call_args
    pos = args[0]
    assert pos.state == "PENDING_ENTRY"


@pytest.mark.asyncio
async def test_event_continuation_winner_calls_try_buy_value(base_ctx):
    sig = MagicMock(spec=EventTriggeredValueSignal)
    sig.token_id = "tok_yes"
    sig.side = "yes"
    sig.is_reversal = False
    sig.is_continuation = True
    sig.fair_price = 0.60
    sig.ask = 0.55
    sig.derived_state_flags = set()
    sig.direction = "radiant"
    sig.game_time_sec = 900
    sig.edge = 0.05
    sig.actual_event_type = "kill"
    sig.signal_id = "sig1"
    sig.lead = 1000
    sig.edge_type = "event"
    sig.target_horizon = "30s"
    sig.expected_hold_sec = 30
    sig.entry_trigger = "e"
    sig.exit_trigger = "x"
    sig.primary_metric = "p"
    sig.secondary_metric = "s"
    sig.promotion_rule = "pr"
    sig.disable_rule = "dr"

    cand = StrategyCandidate(
        strategy="EVENT_CONTINUATION_EDGE", token_id="tok_yes", match_id="m1", 
        direction="radiant", edge=0.05, fair=0.60, game_time_sec=900, signal=sig
    )
    dec = AllocationDecision(token_id="tok_yes", match_id="m1", winner=cand)
    
    attempt = MagicMock()
    attempt.filled_size_usd = 10.0
    attempt.order_status = "filled"
    attempt.match_id = "m1"
    attempt.token_id = "tok_yes"
    base_ctx.live_executor.try_buy_value.return_value = attempt
    
    await execute_allocation_decisions([dec], base_ctx)
    base_ctx.live_executor.try_buy_value.assert_called_once()


@pytest.mark.asyncio
async def test_dswing_winner_calls_try_buy_value(base_ctx):
    sig = MagicMock(spec=DSwingSignal)
    sig.token_id = "tok_yes"
    sig.side = "yes"
    sig.series_fair = 0.70
    sig.ask = 0.65
    sig.direction = "radiant"
    sig.game_time_sec = 900
    sig.edge = 0.05
    sig.signal_id = "sig1"
    sig.lead = 1000
    sig.edge_type = "dswing"
    sig.target_horizon = "end"
    sig.expected_hold_sec = 600
    sig.entry_trigger = "e"
    sig.exit_trigger = "x"
    sig.primary_metric = "p"
    sig.secondary_metric = "s"
    sig.promotion_rule = "pr"
    sig.disable_rule = "dr"
    sig.p_game = 0.70
    sig.book_age_ms = 100

    cand = StrategyCandidate(
        strategy="DSWING", token_id="tok_yes", match_id="m1", 
        direction="radiant", edge=0.05, fair=0.70, game_time_sec=900, signal=sig
    )
    dec = AllocationDecision(token_id="tok_yes", match_id="m1", winner=cand)
    
    attempt = MagicMock()
    attempt.filled_size_usd = 20.0
    attempt.order_status = "filled"
    attempt.match_id = "m1"
    attempt.token_id = "tok_yes"
    base_ctx.live_executor.try_buy_value.return_value = attempt
    
    await execute_allocation_decisions([dec], base_ctx)
    base_ctx.live_executor.try_buy_value.assert_called_once()
