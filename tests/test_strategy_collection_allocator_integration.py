"""Integration tests for strategy_collection and strategy_allocator."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock
from dataclasses import dataclass

from strategy_collection import (
    StrategyCollectionContext,
    StrategyCollectionLoggers,
    collect_strategy_candidates,
)
from strategy_allocator import allocate_candidates
from value_engine import ValueSignal
from event_triggered_value_engine import EventTriggeredValueSignal


@pytest.fixture
def integration_ctx():
    game = {"match_id": "m1", "game_time_sec": 900}
    mapping = {
        "yes_token_id": "tok_A",
        "no_token_id": "tok_B",
        "name": "Team A vs Team B",
    }
    return StrategyCollectionContext(
        game=game,
        mapping=mapping,
        book_store=MagicMock(),
        entered_tokens=set(),
        live_active_tokens=set(),
        enable_value_trading=True,
        enable_event_triggered_value_trading=True,
        loggers=StrategyCollectionLoggers(
            value_logger=MagicMock(),
            strategy_signal_logger=MagicMock(),
        ),
    )


def _make_value_sig(token_id, edge=0.10):
    sig = MagicMock(spec=ValueSignal)
    sig.token_id = token_id
    sig.direction = "radiant"
    sig.edge = edge
    sig.fair_price = 0.80 + edge
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
    return sig


def _make_event_sig(token_id, is_reversal=False, edge=0.15):
    sig = MagicMock(spec=EventTriggeredValueSignal)
    sig.token_id = token_id
    sig.is_reversal = is_reversal
    sig.direction = "radiant"
    sig.edge = edge
    sig.fair_price = 0.80 + edge
    sig.game_time_sec = 900
    sig.actual_event_type = "kill"
    sig.edge_type = "event"
    sig.target_horizon = "30s"
    sig.expected_hold_sec = 30
    sig.entry_trigger = "e"
    sig.exit_trigger = "x"
    sig.primary_metric = "p"
    sig.secondary_metric = "s"
    sig.promotion_rule = "pr"
    sig.disable_rule = "dr"
    return sig


def test_event_continuation_preempts_value_same_token(integration_ctx):
    integration_ctx.value_engine = MagicMock()
    integration_ctx.event_value_engine = MagicMock()
    
    event = MagicMock()
    event.event_id = "e1"
    integration_ctx.game_actual_events = [event]
    
    v_sig = _make_value_sig("tok_A", edge=0.20)
    e_sig = _make_event_sig("tok_A", edge=0.10)
    
    integration_ctx.value_engine.evaluate.return_value = [v_sig]
    integration_ctx.event_value_engine.evaluate.return_value = [e_sig]
    
    candidates = collect_strategy_candidates(integration_ctx)
    assert len(candidates) == 2
    
    decisions = allocate_candidates(candidates, integration_ctx.entered_tokens)
    assert len(decisions) == 1
    d = decisions[0]
    assert d.winner.strategy == "EVENT_CONTINUATION_EDGE"
    assert d.blocked[0].strategy == "VALUE_EDGE"
    assert d.block_reason == "preempted_by_event"


def test_value_preempts_event_reversal_same_token(integration_ctx):
    integration_ctx.value_engine = MagicMock()
    integration_ctx.event_value_engine = MagicMock()
    
    event = MagicMock()
    event.event_id = "e1"
    integration_ctx.game_actual_events = [event]
    
    v_sig = _make_value_sig("tok_A", edge=0.10)
    e_sig = _make_event_sig("tok_A", is_reversal=True, edge=0.20)
    
    integration_ctx.value_engine.evaluate.return_value = [v_sig]
    integration_ctx.event_value_engine.evaluate.return_value = [e_sig]
    
    candidates = collect_strategy_candidates(integration_ctx)
    decisions = allocate_candidates(candidates, integration_ctx.entered_tokens)
    
    assert len(decisions) == 1
    d = decisions[0]
    assert d.winner.strategy == "VALUE_EDGE"
    assert d.blocked[0].strategy == "EVENT_REVERSAL_EDGE"
    assert d.block_reason == "preempted_by_value"


def test_already_entered_token_blocks_all_collected_candidates(integration_ctx):
    integration_ctx.value_engine = MagicMock()
    integration_ctx.entered_tokens = {"tok_A"}
    
    v_sig = _make_value_sig("tok_A")
    integration_ctx.value_engine.evaluate.return_value = [v_sig]
    
    candidates = collect_strategy_candidates(integration_ctx)
    decisions = allocate_candidates(candidates, integration_ctx.entered_tokens)
    
    assert len(decisions) == 1
    d = decisions[0]
    assert d.winner is None
    assert d.block_reason == "already_entered"


def test_uncontested_candidates_survive_allocation(integration_ctx):
    integration_ctx.value_engine = MagicMock()
    integration_ctx.event_value_engine = MagicMock()
    
    event = MagicMock()
    event.event_id = "e1"
    integration_ctx.game_actual_events = [event]
    
    v_sig = _make_value_sig("tok_A")
    e_sig = _make_event_sig("tok_B")
    
    integration_ctx.value_engine.evaluate.return_value = [v_sig]
    integration_ctx.event_value_engine.evaluate.return_value = [e_sig]
    
    candidates = collect_strategy_candidates(integration_ctx)
    decisions = allocate_candidates(candidates, integration_ctx.entered_tokens)
    
    assert len(decisions) == 2
    by_token = {d.token_id: d for d in decisions}
    assert by_token["tok_A"].winner.strategy == "VALUE_EDGE"
    assert by_token["tok_B"].winner.strategy == "EVENT_CONTINUATION_EDGE"
