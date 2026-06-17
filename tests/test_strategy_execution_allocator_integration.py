"""Integration tests for strategy_allocator and strategy_execution."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock
from dataclasses import dataclass

from strategy_allocator import allocate_candidates, StrategyCandidate
from strategy_execution import (
    StrategyExecutionContext,
    StrategyExecutionLoggers,
    execute_allocation_decisions,
)
from value_engine import ValueSignal
from event_triggered_value_engine import EventTriggeredValueSignal
from decisive_swing_engine import DSwingSignal


@pytest.fixture
def integration_ctx():
    return StrategyExecutionContext(
        game={"match_id": "m1"},
        mapping={
            "yes_token_id": "tok_A", 
            "no_token_id": "tok_B", 
            "name": "M1",
            "market_type": "MAP_WINNER"
        },
        book_store=MagicMock(),
        trader=MagicMock(),
        live_executor=AsyncMock(),
        live_position_store=MagicMock(),
        entered_tokens=set(),
        loggers=StrategyExecutionLoggers(),
        normalized_entry_fill_fn=lambda **kwargs: (10.0, 100.0, 0.10),
        annotate_signal_policy_for_paper_fn=lambda **kwargs: kwargs["signal"],
    )


def _make_value_cand(token_id, edge=0.10):
    sig = MagicMock(spec=ValueSignal)
    sig.token_id = token_id
    sig.side = "yes" if token_id == "tok_A" else "no"
    sig.edge = edge
    sig.fair_price = 0.80
    sig.ask = 0.70
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
    sig.signal_id = "sig-v1"
    sig.to_signal_dict.return_value = {"strategy": "value"}
    return StrategyCandidate(
        strategy="VALUE_EDGE", token_id=token_id, match_id="m1", 
        direction="radiant", edge=edge, fair=0.80, game_time_sec=900, 
        signal=sig, would_pass_confirmation=True
    )


def _make_event_cand(token_id, is_reversal=False, edge=0.15):
    sig = MagicMock(spec=EventTriggeredValueSignal)
    sig.token_id = token_id
    sig.side = "yes" if token_id == "tok_A" else "no"
    sig.edge = edge
    sig.is_reversal = is_reversal
    sig.is_continuation = not is_reversal
    sig.fair_price = 0.90
    sig.ask = 0.75
    sig.derived_state_flags = set()
    sig.direction = "radiant"
    sig.game_time_sec = 900
    sig.actual_event_type = "kill"
    sig.signal_id = "sig-e1"
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
    sig.to_signal_dict.return_value = {"strategy": "event"}
    
    strategy = "EVENT_REVERSAL_EDGE" if is_reversal else "EVENT_CONTINUATION_EDGE"
    return StrategyCandidate(
        strategy=strategy, token_id=token_id, match_id="m1", 
        direction="radiant", edge=edge, fair=0.90, game_time_sec=900, signal=sig
    )


def _make_dswing_cand(token_id, edge=0.05):
    sig = MagicMock(spec=DSwingSignal)
    sig.token_id = token_id
    sig.side = "yes" if token_id == "tok_A" else "no"
    sig.edge = edge
    sig.series_fair = 0.85
    sig.ask = 0.80
    sig.direction = "radiant"
    sig.game_time_sec = 1000
    sig.signal_id = "sig-ds1"
    sig.lead = 1500
    sig.edge_type = "dswing"
    sig.target_horizon = "end"
    sig.expected_hold_sec = 600
    sig.entry_trigger = "e"
    sig.exit_trigger = "x"
    sig.primary_metric = "p"
    sig.secondary_metric = "s"
    sig.promotion_rule = "pr"
    sig.disable_rule = "dr"
    sig.p_game = 0.85
    sig.book_age_ms = 100
    
    return StrategyCandidate(
        strategy="DSWING", token_id=token_id, match_id="m1", 
        direction="radiant", edge=edge, fair=0.85, game_time_sec=1000, signal=sig
    )


@pytest.mark.asyncio
async def test_execute_only_allocator_winner_when_event_preempts_value(integration_ctx):
    v_cand = _make_value_cand("tok_A", edge=0.20)
    e_cand = _make_event_cand("tok_A", edge=0.10) # Event has higher priority than Value
    
    candidates = [v_cand, e_cand]
    decisions = allocate_candidates(candidates, integration_ctx.entered_tokens)
    
    attempt = MagicMock()
    attempt.filled_size_usd = 10.0
    attempt.order_status = "filled"
    attempt.match_id = "m1"
    attempt.token_id = "tok_A"
    integration_ctx.live_executor.try_buy_value.return_value = attempt
    
    await execute_allocation_decisions(decisions, integration_ctx)
    
    assert integration_ctx.live_executor.try_buy_value.call_count == 1
    _, kwargs = integration_ctx.live_executor.try_buy_value.call_args
    assert kwargs["signal"] is e_cand.signal


@pytest.mark.asyncio
async def test_execute_only_value_when_value_preempts_event_reversal(integration_ctx):
    v_cand = _make_value_cand("tok_A", edge=0.10)
    e_cand = _make_event_cand("tok_A", is_reversal=True, edge=0.20) # Value > Reversal
    
    candidates = [v_cand, e_cand]
    decisions = allocate_candidates(candidates, integration_ctx.entered_tokens)
    
    attempt = MagicMock()
    attempt.filled_size_usd = 10.0
    attempt.order_status = "filled"
    attempt.match_id = "m1"
    attempt.token_id = "tok_A"
    integration_ctx.live_executor.try_buy_value.return_value = attempt
    
    await execute_allocation_decisions(decisions, integration_ctx)
    
    assert integration_ctx.live_executor.try_buy_value.call_count == 1
    _, kwargs = integration_ctx.live_executor.try_buy_value.call_args
    assert kwargs["signal"] is v_cand.signal


@pytest.mark.asyncio
async def test_execute_only_event_reversal_when_it_preempts_dswing(integration_ctx):
    e_cand = _make_event_cand("tok_A", is_reversal=True, edge=0.20)
    d_cand = _make_dswing_cand("tok_A", edge=0.10) # Reversal > DSwing
    
    candidates = [e_cand, d_cand]
    decisions = allocate_candidates(candidates, integration_ctx.entered_tokens)
    
    attempt = MagicMock()
    attempt.filled_size_usd = 10.0
    attempt.order_status = "filled"
    attempt.match_id = "m1"
    attempt.token_id = "tok_A"
    integration_ctx.live_executor.try_buy_value.return_value = attempt
    
    await execute_allocation_decisions(decisions, integration_ctx)
    
    assert integration_ctx.live_executor.try_buy_value.call_count == 1
    _, kwargs = integration_ctx.live_executor.try_buy_value.call_args
    assert kwargs["signal"] is e_cand.signal


@pytest.mark.asyncio
async def test_execute_nothing_for_already_entered_token(integration_ctx):
    integration_ctx.entered_tokens = {"tok_A"}
    v_cand = _make_value_cand("tok_A")
    
    candidates = [v_cand]
    decisions = allocate_candidates(candidates, integration_ctx.entered_tokens)
    
    await execute_allocation_decisions(decisions, integration_ctx)
    
    integration_ctx.live_executor.try_buy_value.assert_not_called()


@pytest.mark.asyncio
async def test_execute_multiple_uncontested_winners(integration_ctx):
    v_cand = _make_value_cand("tok_A")
    e_cand = _make_event_cand("tok_B")
    
    candidates = [v_cand, e_cand]
    decisions = allocate_candidates(candidates, integration_ctx.entered_tokens)
    
    attempt = MagicMock()
    attempt.filled_size_usd = 10.0
    attempt.order_status = "filled"
    attempt.match_id = "m1"
    
    integration_ctx.live_executor.try_buy_value.return_value = attempt
    
    await execute_allocation_decisions(decisions, integration_ctx)
    
    assert integration_ctx.live_executor.try_buy_value.call_count == 2
    signals = [ca.kwargs["signal"] for ca in integration_ctx.live_executor.try_buy_value.call_args_list]
    assert v_cand.signal in signals
    assert e_cand.signal in signals
