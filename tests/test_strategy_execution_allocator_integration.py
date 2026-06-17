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


@pytest.fixture
def integration_ctx():
    return StrategyExecutionContext(
        game={"match_id": "m1"},
        mapping={"yes_token_id": "tok_A", "no_token_id": "tok_B", "name": "M1"},
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
    sig.to_signal_dict.return_value = {"strategy": "value"}
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
    sig.to_signal_dict.return_value = {"strategy": "event"}
    sig.direction = "radiant"
    sig.game_time_sec = 900
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

    strategy = "EVENT_REVERSAL_EDGE" if is_reversal else "EVENT_CONTINUATION_EDGE"
    return StrategyCandidate(
        strategy=strategy, token_id=token_id, match_id="m1", 
        direction="radiant", edge=edge, fair=0.90, game_time_sec=900, signal=sig
    )


@pytest.mark.asyncio
async def test_execute_only_allocator_winner_when_event_preempts_value(integration_ctx):
    # Same token, event preempts value
    v_cand = _make_value_cand("tok_A", edge=0.20)
    e_cand = _make_event_cand("tok_A", edge=0.10)
    
    candidates = [v_cand, e_cand]
    decisions = allocate_candidates(candidates, integration_ctx.entered_tokens)
    
    # Mock executor to return landing attempt
    attempt = MagicMock()
    attempt.filled_size_usd = 10.0
    attempt.order_status = "filled"
    attempt.match_id = "m1"
    attempt.token_id = "tok_A"
    integration_ctx.live_executor.try_buy_value.return_value = attempt
    
    await execute_allocation_decisions(decisions, integration_ctx)
    
    # try_buy_value should only be called for the event winner
    assert integration_ctx.live_executor.try_buy_value.call_count == 1
    args, kwargs = integration_ctx.live_executor.try_buy_value.call_args
    assert kwargs["signal"] == e_cand.signal


@pytest.mark.asyncio
async def test_execute_only_value_when_value_preempts_event_reversal(integration_ctx):
    # Same token, value preempts event reversal
    v_cand = _make_value_cand("tok_A", edge=0.10)
    e_cand = _make_event_cand("tok_A", is_reversal=True, edge=0.20)
    
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
    args, kwargs = integration_ctx.live_executor.try_buy_value.call_args
    assert kwargs["signal"] == v_cand.signal


@pytest.mark.asyncio
async def test_execute_nothing_for_already_entered_token(integration_ctx):
    integration_ctx.entered_tokens = {"tok_A"}
    v_cand = _make_value_cand("tok_A")
    
    candidates = [v_cand]
    decisions = allocate_candidates(candidates, integration_ctx.entered_tokens)
    
    await execute_allocation_decisions(decisions, integration_ctx)
    
    integration_ctx.live_executor.try_buy_value.assert_not_called()


@pytest.mark.asyncio
async def test_execute_uncontested_multiple_tokens(integration_ctx):
    v_cand = _make_value_cand("tok_A")
    e_cand = _make_event_cand("tok_B")
    
    candidates = [v_cand, e_cand]
    decisions = allocate_candidates(candidates, integration_ctx.entered_tokens)
    
    attempt = MagicMock()
    attempt.filled_size_usd = 10.0
    attempt.order_status = "filled"
    attempt.match_id = "m1"
    base_ret = attempt
    
    # We need to return different attempts for different tokens or just mock them
    integration_ctx.live_executor.try_buy_value.side_effect = [base_ret, base_ret]
    
    await execute_allocation_decisions(decisions, integration_ctx)
    
    assert integration_ctx.live_executor.try_buy_value.call_count == 2
