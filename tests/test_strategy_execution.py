"""Harden strategy execution parity tests.

Verifies that LivePosition fields and execution paths in strategy_execution.py
match the original runtime behavior exactly.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock
from dataclasses import dataclass

from strategy_execution import (
    StrategyExecutionContext,
    StrategyExecutionLoggers,
    execute_allocation_decisions,
    StrategyExecutionResult,
)
from strategy_allocator import AllocationDecision, StrategyCandidate
from value_engine import ValueSignal
from decisive_swing_engine import DSwingSignal
from event_triggered_value_engine import EventTriggeredValueSignal
from live_position_store import LivePosition


# ── HELPERS ───────────────────────────────────────────────────────────────────

def make_value_signal(token_id="tok_yes", side="yes", edge=0.02, fair=0.50, ask=0.48):
    sig = MagicMock(spec=ValueSignal)
    sig.token_id = token_id
    sig.side = side
    sig.fair_price = fair
    sig.ask = ask
    sig.edge = edge
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
    sig.to_signal_dict.return_value = {"signal_id": sig.signal_id}
    return sig


def make_event_signal(token_id="tok_yes", side="yes", is_reversal=False, edge=0.05, fair=0.60, ask=0.55):
    sig = MagicMock(spec=EventTriggeredValueSignal)
    sig.token_id = token_id
    sig.side = side
    sig.is_reversal = is_reversal
    sig.is_continuation = not is_reversal
    sig.fair_price = fair
    sig.ask = ask
    sig.edge = edge
    sig.direction = "radiant"
    sig.game_time_sec = 950
    sig.actual_event_type = "kill"
    sig.signal_id = "sig-e1"
    sig.lead = 1200
    sig.edge_type = "event"
    sig.target_horizon = "30s"
    sig.expected_hold_sec = 30
    sig.entry_trigger = "e"
    sig.exit_trigger = "x"
    sig.primary_metric = "p"
    sig.secondary_metric = "s"
    sig.promotion_rule = "pr"
    sig.disable_rule = "dr"
    sig.derived_state_flags = {"flag1"}
    sig.to_signal_dict.return_value = {"signal_id": sig.signal_id}
    return sig


def make_dswing_signal(token_id="tok_yes", side="yes", edge=0.08, fair=0.70, ask=0.62):
    sig = MagicMock(spec=DSwingSignal)
    sig.token_id = token_id
    sig.side = side
    sig.series_fair = fair
    sig.ask = ask
    sig.edge = edge
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
    sig.p_game = 0.75
    sig.book_age_ms = 150
    sig.would_pass_live_gates = True
    sig.would_pass_live = True
    sig.live_skip_reason = ""
    sig.paper_only_bypass = False
    sig.policy_allowed = True
    sig.policy_reason = ""
    sig.policy_version = "v1"
    sig.risk_tags = []
    sig.p_game_used = 0.75
    sig.sized_usd = 25.0
    return sig


def make_live_attempt(status="filled", filled=10.0, price=0.50, order_id="ord-1"):
    attempt = MagicMock()
    attempt.order_status = status
    attempt.filled_size_usd = filled
    attempt.submitted_size_usd = 10.0
    attempt.avg_fill_price = price
    # If price is None, default cap to a reasonable value or None
    attempt.price_cap = (price + 0.01) if price is not None else 0.51
    attempt.match_id = "m1"
    attempt.token_id = "tok_yes"
    attempt.created_at_ns = 123456789
    attempt.order_id = order_id
    attempt.reason_if_rejected = ""
    return attempt


@pytest.fixture
def base_ctx():
    return StrategyExecutionContext(
        game={"match_id": "m1"},
        mapping={
            "yes_token_id": "tok_yes",
            "no_token_id": "tok_no",
            "name": "M1",
            "market_type": "MAP_WINNER",
            "series_score_yes": 1,
            "series_score_no": 0,
            "current_game_number": 2,
        },
        book_store=MagicMock(),
        trader=MagicMock(),
        live_executor=AsyncMock(),
        live_position_store=MagicMock(),
        entered_tokens=set(),
        loggers=StrategyExecutionLoggers(
            live_logger=MagicMock(),
            position_logger=MagicMock(),
        ),
        normalized_entry_fill_fn=MagicMock(return_value=(10.0, 20.0, 0.50)),
        annotate_signal_policy_for_paper_fn=lambda **kwargs: kwargs["signal"],
        enable_real_live_trading=False,
    )


# ── VALUE TESTS ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_value_live_filled_position_fields(base_ctx):
    sig = make_value_signal()
    cand = StrategyCandidate(
        strategy="VALUE_EDGE", token_id="tok_yes", match_id="m1", 
        direction="radiant", edge=sig.edge, fair=sig.fair_price, game_time_sec=sig.game_time_sec, 
        signal=sig, would_pass_confirmation=True
    )
    dec = AllocationDecision(token_id="tok_yes", match_id="m1", winner=cand)
    
    attempt = make_live_attempt(status="filled", filled=10.0, price=0.50)
    base_ctx.live_executor.try_buy_value.return_value = attempt
    
    await execute_allocation_decisions([dec], base_ctx)
    
    # Verify LivePosition construction
    base_ctx.live_position_store.add.assert_called_once()
    pos: LivePosition = base_ctx.live_position_store.add.call_args[0][0]
    
    assert pos.state == "OPEN"
    assert pos.pending_entry_order_id is None
    assert pos.strategy_kind == "VALUE_EDGE"
    assert pos.strategy_family == "VALUE"
    assert pos.entry_engine == "value"
    assert pos.exit_engine == "value_fair_invalidation"
    assert pos.hold_policy == "thesis_invalidation"
    assert pos.trader_kind == "value"
    assert pos.entry_fair == sig.fair_price
    assert pos.entry_edge == sig.edge
    assert pos.entry_ask == sig.ask
    assert pos.shares == 20.0
    assert pos.cost_usd == 10.0
    assert pos.entry_price == 0.50
    assert "tok_yes" in base_ctx.entered_tokens


@pytest.mark.asyncio
async def test_value_live_delayed_position_fields(base_ctx):
    sig = make_value_signal()
    cand = StrategyCandidate(
        strategy="VALUE_EDGE", token_id="tok_yes", match_id="m1", 
        direction="radiant", edge=sig.edge, fair=sig.fair_price, game_time_sec=sig.game_time_sec, 
        signal=sig, would_pass_confirmation=True
    )
    dec = AllocationDecision(token_id="tok_yes", match_id="m1", winner=cand)
    
    attempt = make_live_attempt(status="delayed", filled=0.0, price=None, order_id="ord-delayed")
    base_ctx.live_executor.try_buy_value.return_value = attempt
    base_ctx.normalized_entry_fill_fn.return_value = None # No fill yet
    
    await execute_allocation_decisions([dec], base_ctx)
    
    pos: LivePosition = base_ctx.live_position_store.add.call_args[0][0]
    assert pos.state == "PENDING_ENTRY"
    assert pos.pending_entry_order_id == "ord-delayed"
    assert pos.shares == 0.0
    assert pos.cost_usd == attempt.submitted_size_usd
    assert pos.entry_price == attempt.price_cap


@pytest.mark.asyncio
async def test_value_live_rejected_attempt_creates_no_position(base_ctx):
    sig = make_value_signal()
    cand = StrategyCandidate(
        strategy="VALUE_EDGE", token_id="tok_yes", match_id="m1", 
        direction="radiant", edge=sig.edge, fair=sig.fair_price, game_time_sec=sig.game_time_sec, 
        signal=sig, would_pass_confirmation=True
    )
    dec = AllocationDecision(token_id="tok_yes", match_id="m1", winner=cand)
    
    attempt = make_live_attempt(status="rejected", filled=0.0)
    attempt.reason_if_rejected = "price_moved"
    base_ctx.live_executor.try_buy_value.return_value = attempt
    
    results = await execute_allocation_decisions([dec], base_ctx)
    assert results[0].status == "rejected"
    assert results[0].reason == "price_moved"
    base_ctx.live_position_store.add.assert_not_called()


@pytest.mark.asyncio
async def test_value_waiting_confirmation_does_not_call_executor(base_ctx):
    sig = make_value_signal()
    cand = StrategyCandidate(
        strategy="VALUE_EDGE", token_id="tok_yes", match_id="m1", 
        direction="radiant", edge=sig.edge, fair=sig.fair_price, game_time_sec=sig.game_time_sec, 
        signal=sig, would_pass_confirmation=False
    )
    dec = AllocationDecision(token_id="tok_yes", match_id="m1", winner=cand)
    
    results = await execute_allocation_decisions([dec], base_ctx)
    assert results[0].status == "waiting_confirmation"
    base_ctx.live_executor.try_buy_value.assert_not_called()


@pytest.mark.asyncio
async def test_value_paper_entry_uses_annotation_and_logs_position(base_ctx):
    base_ctx.live_executor = None # Force paper
    sig = make_value_signal()
    cand = StrategyCandidate(
        strategy="VALUE_EDGE", token_id="tok_yes", match_id="m1", 
        direction="radiant", edge=sig.edge, fair=sig.fair_price, game_time_sec=sig.game_time_sec, 
        signal=sig, would_pass_confirmation=True
    )
    dec = AllocationDecision(token_id="tok_yes", match_id="m1", winner=cand)
    
    pos = MagicMock(spec=LivePosition)
    pos.position_id = "paper-p1"
    pos.entry_price = 0.48
    base_ctx.trader.enter.return_value = (pos, "ok")
    
    await execute_allocation_decisions([dec], base_ctx)
    base_ctx.trader.enter.assert_called_once()
    base_ctx.loggers.position_logger.log_entry.assert_called_once_with(pos)


# ── EVENT TESTS ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_event_continuation_live_filled_position_fields(base_ctx):
    sig = make_event_signal(is_reversal=False)
    cand = StrategyCandidate(
        strategy="EVENT_CONTINUATION_EDGE", token_id="tok_yes", match_id="m1", 
        direction=sig.direction, edge=sig.edge, fair=sig.fair_price, game_time_sec=sig.game_time_sec, signal=sig
    )
    dec = AllocationDecision(token_id="tok_yes", match_id="m1", winner=cand)
    
    attempt = make_live_attempt(status="filled", filled=10.0, price=0.55)
    base_ctx.live_executor.try_buy_value.return_value = attempt
    base_ctx.normalized_entry_fill_fn.return_value = (10.0, 18.18, 0.55)
    
    await execute_allocation_decisions([dec], base_ctx)
    
    pos: LivePosition = base_ctx.live_position_store.add.call_args[0][0]
    assert pos.strategy_kind == "EVENT_CONTINUATION_EDGE"
    assert pos.strategy_family == "EVENT"
    assert pos.strategy_subtype == sig.actual_event_type
    assert pos.entry_is_reversal is False
    assert pos.entry_is_continuation is True
    assert pos.entry_engine == "event_triggered_value"
    assert pos.exit_engine == "value_fair_invalidation"
    assert pos.hold_policy == "thesis_invalidation"
    assert pos.event_type == "EVENT_CONTINUATION_EDGE"
    assert pos.entry_actual_event_type == sig.actual_event_type
    assert pos.entry_derived_state_flags == ["flag1"]


@pytest.mark.asyncio
async def test_event_reversal_live_filled_position_fields(base_ctx):
    sig = make_event_signal(is_reversal=True)
    cand = StrategyCandidate(
        strategy="EVENT_REVERSAL_EDGE", token_id="tok_yes", match_id="m1", 
        direction=sig.direction, edge=sig.edge, fair=sig.fair_price, game_time_sec=sig.game_time_sec, signal=sig
    )
    dec = AllocationDecision(token_id="tok_yes", match_id="m1", winner=cand)
    
    attempt = make_live_attempt(status="filled", filled=10.0, price=0.55)
    base_ctx.live_executor.try_buy_value.return_value = attempt
    
    await execute_allocation_decisions([dec], base_ctx)
    
    pos: LivePosition = base_ctx.live_position_store.add.call_args[0][0]
    assert pos.strategy_kind == "EVENT_REVERSAL_EDGE"
    assert pos.entry_is_reversal is True
    assert pos.exit_engine == "event_reversal_exit"
    assert pos.hold_policy == "reversal_bounce_or_thesis"
    assert pos.event_type == "EVENT_REVERSAL_EDGE"


# ── DSWING TESTS ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dswing_live_filled_position_fields(base_ctx):
    sig = make_dswing_signal()
    cand = StrategyCandidate(
        strategy="DSWING", token_id="tok_yes", match_id="m1", 
        direction=sig.direction, edge=sig.edge, fair=sig.series_fair, game_time_sec=sig.game_time_sec, signal=sig
    )
    dec = AllocationDecision(token_id="tok_yes", match_id="m1", winner=cand)
    
    attempt = make_live_attempt(status="filled", filled=25.0, price=0.62)
    base_ctx.live_executor.try_buy_value.return_value = attempt
    base_ctx.normalized_entry_fill_fn.return_value = (25.0, 40.32, 0.62)
    
    await execute_allocation_decisions([dec], base_ctx)
    
    pos: LivePosition = base_ctx.live_position_store.add.call_args[0][0]
    assert pos.state == "OPEN"
    assert pos.strategy_kind == "DSWING"
    assert pos.strategy_family == "DSWING"
    assert pos.entry_engine == "decisive_swing"
    assert pos.exit_engine == "dswing_map_end"
    assert pos.hold_policy == "map_end_convergence"
    assert pos.event_type == "DSWING"
    assert pos.trader_kind == "dswing"
    assert pos.entry_fair == sig.series_fair
    assert pos.entry_p_game == sig.p_game
    assert pos.entry_market_type == "MAP_WINNER"
    assert pos.pending_entry_order_id is None


@pytest.mark.asyncio
async def test_dswing_live_delayed_position_fields(base_ctx):
    sig = make_dswing_signal()
    cand = StrategyCandidate(
        strategy="DSWING", token_id="tok_yes", match_id="m1", 
        direction=sig.direction, edge=sig.edge, fair=sig.series_fair, game_time_sec=sig.game_time_sec, signal=sig
    )
    dec = AllocationDecision(token_id="tok_yes", match_id="m1", winner=cand)
    
    attempt = make_live_attempt(status="delayed", filled=0.0, order_id="ord-ds-delayed")
    base_ctx.live_executor.try_buy_value.return_value = attempt
    base_ctx.normalized_entry_fill_fn.return_value = None
    
    await execute_allocation_decisions([dec], base_ctx)
    
    pos: LivePosition = base_ctx.live_position_store.add.call_args[0][0]
    assert pos.state == "PENDING_ENTRY"
    assert pos.pending_entry_order_id == "ord-ds-delayed"
