"""Tests for strategy_allocator.py."""
from __future__ import annotations

import pytest
from dataclasses import make_dataclass

from strategy_allocator import (
    StrategyCandidate,
    AllocationDecision,
    allocate_candidates,
    decision_to_log_row,
    _priority,
)


# ---------------------------------------------------------------------------
# Minimal fake signal
# ---------------------------------------------------------------------------

FakeSig = make_dataclass("FakeSig", [("token_id", str), ("match_id", str), ("edge", float)])


def _cand(strategy: str, token_id: str = "tok_A", match_id: str = "m1",
          direction: str = "radiant", edge: float = 0.10, fair: float = 0.80,
          game_time_sec: int = 900, **kwargs) -> StrategyCandidate:
    return StrategyCandidate(
        strategy=strategy,
        token_id=token_id,
        match_id=match_id,
        direction=direction,
        edge=edge,
        fair=fair,
        game_time_sec=game_time_sec,
        signal=FakeSig(token_id, match_id, edge),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------

def test_priority_ordering():
    assert _priority(_cand("EVENT_CONTINUATION_EDGE")) < _priority(_cand("VALUE_EDGE"))
    assert _priority(_cand("VALUE_EDGE")) < _priority(_cand("EVENT_REVERSAL_EDGE"))
    assert _priority(_cand("EVENT_REVERSAL_EDGE")) < _priority(_cand("DSWING"))


# ---------------------------------------------------------------------------
# test_single_candidate_no_blocking
# ---------------------------------------------------------------------------

def test_single_candidate_no_blocking():
    """One VALUE_EDGE candidate, not in entered_tokens → wins with empty blocked list."""
    c = _cand("VALUE_EDGE", token_id="tok_A")
    decisions = allocate_candidates([c], entered_tokens=set())
    assert len(decisions) == 1
    d = decisions[0]
    assert d.winner is c
    assert d.blocked == []
    assert d.block_reason == ""
    row = decision_to_log_row(d)
    assert row is not None
    assert row["candidate_count"] == 1


# ---------------------------------------------------------------------------
# test_event_preempts_value
# ---------------------------------------------------------------------------

def test_event_preempts_value():
    """EVENT + VALUE on same token → EVENT wins, VALUE in blocked."""
    c_event = _cand("EVENT_CONTINUATION_EDGE", token_id="tok_A", edge=0.12)
    c_value = _cand("VALUE_EDGE", token_id="tok_A", edge=0.10)
    decisions = allocate_candidates([c_event, c_value], entered_tokens=set())
    assert len(decisions) == 1
    d = decisions[0]
    assert d.winner is c_event
    assert len(d.blocked) == 1
    assert d.blocked[0] is c_value
    assert d.block_reason == "preempted_by_event"
    row = decision_to_log_row(d)
    assert row is not None
    assert row["winner_strategy"] == "EVENT_CONTINUATION_EDGE"
    assert "VALUE_EDGE" in row["blocked_strategies"]


# ---------------------------------------------------------------------------
# test_value_preempts_reversal
# ---------------------------------------------------------------------------

def test_value_preempts_reversal():
    """VALUE_EDGE wins over EVENT_REVERSAL_EDGE on same token."""
    c_value = _cand("VALUE_EDGE", token_id="tok_A", edge=0.10)
    c_rev = _cand("EVENT_REVERSAL_EDGE", token_id="tok_A", edge=0.15)
    decisions = allocate_candidates([c_rev, c_value], entered_tokens=set())
    assert len(decisions) == 1
    d = decisions[0]
    assert d.winner is c_value
    assert len(d.blocked) == 1
    assert d.blocked[0] is c_rev
    assert d.block_reason == "preempted_by_value"
    row = decision_to_log_row(d)
    assert row is not None
    assert row["winner_strategy"] == "VALUE_EDGE"
    assert "EVENT_REVERSAL_EDGE" in row["blocked_strategies"]


# ---------------------------------------------------------------------------
# test_reversal_preempts_value_lower_priority_than_continuation
# ---------------------------------------------------------------------------

def test_reversal_lower_priority_than_continuation():
    """EVENT_CONTINUATION_EDGE wins over EVENT_REVERSAL_EDGE on same token."""
    c_cont = _cand("EVENT_CONTINUATION_EDGE", token_id="tok_A", edge=0.08)
    c_rev = _cand("EVENT_REVERSAL_EDGE", token_id="tok_A", edge=0.15)
    decisions = allocate_candidates([c_cont, c_rev], entered_tokens=set())
    d = decisions[0]
    assert d.winner is c_cont
    assert d.blocked[0] is c_rev


# ---------------------------------------------------------------------------
# test_already_entered
# ---------------------------------------------------------------------------

def test_already_entered():
    """Token in entered_tokens → all candidates blocked with already_entered."""
    c1 = _cand("EVENT_CONTINUATION_EDGE", token_id="tok_A")
    c2 = _cand("VALUE_EDGE", token_id="tok_A")
    decisions = allocate_candidates([c1, c2], entered_tokens={"tok_A"})
    assert len(decisions) == 1
    d = decisions[0]
    assert d.winner is None
    assert d.block_reason == "already_entered"
    assert len(d.blocked) == 2
    row = decision_to_log_row(d)
    assert row is not None
    assert row["block_reason"] == "already_entered"


# ---------------------------------------------------------------------------
# test_value_no_confirmation_still_collected
# ---------------------------------------------------------------------------

def test_value_no_confirmation_still_collectable():
    """VALUE candidate with would_pass_confirmation=False is still a StrategyCandidate."""
    c = _cand("VALUE_EDGE", would_pass_confirmation=False)
    decisions = allocate_candidates([c], entered_tokens=set())
    d = decisions[0]
    # Allocator lets it win (confirmation is enforced in the execute phase).
    assert d.winner is c
    assert d.winner.would_pass_confirmation is False


# ---------------------------------------------------------------------------
# test_different_tokens_both_win
# ---------------------------------------------------------------------------

def test_different_tokens_both_win():
    """EVENT on token A, VALUE on token B → two separate uncontested winners."""
    c_event = _cand("EVENT_CONTINUATION_EDGE", token_id="tok_A")
    c_value = _cand("VALUE_EDGE", token_id="tok_B")
    decisions = allocate_candidates([c_event, c_value], entered_tokens=set())
    assert len(decisions) == 2
    by_token = {d.token_id: d for d in decisions}
    assert by_token["tok_A"].winner is c_event
    assert by_token["tok_B"].winner is c_value
    assert by_token["tok_A"].blocked == []
    assert by_token["tok_B"].blocked == []


# ---------------------------------------------------------------------------
# test_dswing_preempted_by_event
# ---------------------------------------------------------------------------

def test_dswing_preempted_by_event():
    """DSWING + EVENT on same token → EVENT wins."""
    c_event = _cand("EVENT_CONTINUATION_EDGE", token_id="tok_A")
    c_dswing = _cand("DSWING", token_id="tok_A")
    decisions = allocate_candidates([c_dswing, c_event], entered_tokens=set())
    d = decisions[0]
    assert d.winner.strategy == "EVENT_CONTINUATION_EDGE"
    assert any(b.strategy == "DSWING" for b in d.blocked)
    assert d.block_reason == "preempted_by_event"


# ---------------------------------------------------------------------------
# test_priority_order_all_three
# ---------------------------------------------------------------------------

def test_priority_order_all_three():
    """EVENT + VALUE + DSWING on same token → EVENT wins, VALUE + DSWING both blocked."""
    c_event = _cand("EVENT_CONTINUATION_EDGE", token_id="tok_A", edge=0.11)
    c_value = _cand("VALUE_EDGE", token_id="tok_A", edge=0.13)
    c_dswing = _cand("DSWING", token_id="tok_A", edge=0.09)
    decisions = allocate_candidates([c_value, c_dswing, c_event], entered_tokens=set())
    assert len(decisions) == 1
    d = decisions[0]
    assert d.winner.strategy == "EVENT_CONTINUATION_EDGE"
    blocked_strats = {b.strategy for b in d.blocked}
    assert "VALUE_EDGE" in blocked_strats
    assert "DSWING" in blocked_strats
    assert d.block_reason == "preempted_by_event"


# ---------------------------------------------------------------------------
# test_empty_input
# ---------------------------------------------------------------------------

def test_empty_input():
    """No candidates → empty decisions list."""
    decisions = allocate_candidates([], entered_tokens=set())
    assert decisions == []


# ---------------------------------------------------------------------------
# test_decision_to_log_row_uncontested_returns_none
# ---------------------------------------------------------------------------

def test_decision_to_log_row_uncontested_default_include():
    """Uncontested winner produces a log row by default."""
    c = _cand("VALUE_EDGE")
    d = AllocationDecision(token_id="tok_A", match_id="m1", winner=c, blocked=[])
    row = decision_to_log_row(d)
    assert row is not None
    assert row["candidate_count"] == 1
    assert row["blocked_count"] == 0
    assert row["allocator_winner"] == "VALUE_EDGE"


# ---------------------------------------------------------------------------
# test_decision_to_log_row_fields
# ---------------------------------------------------------------------------

def test_decision_to_log_row_fields():
    """Log row has expected keys when preemption occurs."""
    c_event = _cand("EVENT_CONTINUATION_EDGE", edge=0.12)
    c_value = _cand("VALUE_EDGE", edge=0.10)
    decisions = allocate_candidates([c_event, c_value], entered_tokens=set())
    row = decision_to_log_row(decisions[0])
    assert row is not None
    assert row["candidate_count"] == 2
    assert row["blocked_count"] == 1
    for key in ("token_id", "match_id", "winner_strategy", "winner_edge", "candidate_count", "blocked_count", "allocator_winner",
                "blocked_strategies", "blocked_edges", "block_reason", "counterfactual_note"):
        assert key in row, f"Missing key: {key}"

def test_model_value_blocked_by_active_match():
    # If match already has a MODEL_VALUE_EDGE, block
    c = _cand("MODEL_VALUE_EDGE", token_id="tok_A", match_id="m1")
    decisions = allocate_candidates([c], entered_tokens=set(), active_model_value_matches={"m1"})
    assert len(decisions) == 1
    dec = decisions[0]
    assert dec.winner is None
    assert dec.block_reason == "match_exposure_blocked"
    assert c in dec.blocked

def test_model_value_blocked_by_opposing_token():
    # If match has ANY active token, block
    c = _cand("MODEL_VALUE_EDGE", token_id="tok_A", match_id="m1")
    decisions = allocate_candidates([c], entered_tokens=set(), active_match_tokens={"m1": {"tok_B"}})
    assert len(decisions) == 1
    dec = decisions[0]
    assert dec.winner is None
    assert dec.block_reason == "match_exposure_blocked"
    assert c in dec.blocked

def test_model_value_two_candidates_same_match():
    # If two MODEL_VALUE_EDGE candidates appear for the same match, only one wins
    c1 = _cand("MODEL_VALUE_EDGE", token_id="tok_A", match_id="m1")
    c2 = _cand("MODEL_VALUE_EDGE", token_id="tok_B", match_id="m1")
    decisions = allocate_candidates([c1, c2], entered_tokens=set())
    # Two tokens, so two decisions
    assert len(decisions) == 2
    winners = [d.winner for d in decisions if d.winner is not None]
    assert len(winners) == 1
    assert winners[0] in [c1, c2]
    # The other one should be blocked by match_exposure_blocked
    blocked_decs = [d for d in decisions if d.winner is None]
    assert len(blocked_decs) == 1
    assert blocked_decs[0].block_reason == "match_exposure_blocked"
