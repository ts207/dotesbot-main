import pytest
from strategy_allocator import StrategyCandidate, allocate_candidates, AllocationDecision

class FakeSignal:
    def __init__(self, token_id, ask=0.50, edge=0.20):
        self.token_id = token_id
        self.ask = ask
        self.edge = edge

def test_model_value_preempts_value_edge():
    # Candidates for the same token A
    c_model = StrategyCandidate(
        strategy="MODEL_VALUE_EDGE",
        token_id="tok_A",
        match_id="m1",
        direction="radiant",
        edge=0.20,
        fair=0.70,
        game_time_sec=600,
        signal=FakeSignal("tok_A")
    )
    c_value = StrategyCandidate(
        strategy="VALUE_EDGE",
        token_id="tok_A",
        match_id="m1",
        direction="radiant",
        edge=0.30, # higher edge, but lower priority strategy
        fair=0.80,
        game_time_sec=600,
        signal=FakeSignal("tok_A")
    )
    
    decisions = allocate_candidates([c_model, c_value], entered_tokens=set())
    assert len(decisions) == 1
    dec = decisions[0]
    assert dec.winner == c_model
    assert dec.blocked == [c_value]
    assert dec.block_reason == "preempted_by_model"

def test_event_continuation_preempts_model_value():
    c_event = StrategyCandidate(
        strategy="EVENT_CONTINUATION_EDGE",
        token_id="tok_A",
        match_id="m1",
        direction="radiant",
        edge=0.10,
        fair=0.60,
        game_time_sec=600,
        signal=FakeSignal("tok_A")
    )
    c_model = StrategyCandidate(
        strategy="MODEL_VALUE_EDGE",
        token_id="tok_A",
        match_id="m1",
        direction="radiant",
        edge=0.25,
        fair=0.75,
        game_time_sec=600,
        signal=FakeSignal("tok_A")
    )
    
    decisions = allocate_candidates([c_event, c_model], entered_tokens=set())
    assert len(decisions) == 1
    dec = decisions[0]
    assert dec.winner == c_event
    assert dec.blocked == [c_model]
    assert dec.block_reason == "preempted_by_event"

def test_already_entered_token_blocks_all_candidates():
    c_model = StrategyCandidate(
        strategy="MODEL_VALUE_EDGE",
        token_id="tok_A",
        match_id="m1",
        direction="radiant",
        edge=0.20,
        fair=0.70,
        game_time_sec=600,
        signal=FakeSignal("tok_A")
    )
    
    decisions = allocate_candidates([c_model], entered_tokens={"tok_A"})
    assert len(decisions) == 1
    dec = decisions[0]
    assert dec.winner is None
    assert dec.blocked == [c_model]
    assert dec.block_reason == "already_entered"
