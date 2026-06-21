import pytest
import time
from unittest.mock import MagicMock
from model_value_engine import _model_value_confirmation_passes, ModelValueSignal, _MODEL_VALUE_CONFIRM_STATE

@pytest.fixture(autouse=True)
def clean_state():
    _MODEL_VALUE_CONFIRM_STATE.clear()
    yield
    _MODEL_VALUE_CONFIRM_STATE.clear()

def make_test_signal(match_id="m1", token_id="t1", side="YES", edge=0.20, ask=0.50, received_at_ns=None):
    if received_at_ns is None:
        received_at_ns = time.time_ns()
    return ModelValueSignal(
        signal_id=f"sig_{token_id}_{received_at_ns}",
        match_id=match_id,
        received_at_ns=received_at_ns,
        direction="radiant" if side == "YES" else "dire",
        side=side,
        token_id=token_id,
        fair_price=ask + edge,
        ask=ask,
        edge=edge,
        game_time_sec=600,
        book_age_ms=10,
        model_version="v1",
        model_reason="ok",
        sized_usd=5.0,
        token_net_worth_lead=0.0,
        token_score_margin=0.0,
        radiant_net_worth=0.0,
        dire_net_worth=0.0,
        radiant_score=0.0,
        dire_score=0.0,
    )

def test_first_signal_arms_but_does_not_confirm():
    sig = make_test_signal()
    confirmed, reason = _model_value_confirmation_passes(sig)
    assert confirmed is False
    assert reason == "model_value_confirm_armed"
    
    key = f"{sig.match_id}|{sig.token_id}|{sig.side}"
    assert key in _MODEL_VALUE_CONFIRM_STATE
    assert _MODEL_VALUE_CONFIRM_STATE[key]["ask"] == sig.ask

def test_second_signal_confirms():
    sig1 = make_test_signal(ask=0.50, edge=0.20)
    confirmed1, reason1 = _model_value_confirmation_passes(sig1)
    assert confirmed1 is False
    
    # 2 seconds later, ask same, edge same
    sig2 = make_test_signal(ask=0.50, edge=0.20, received_at_ns=sig1.received_at_ns + 2 * 1_000_000_000)
    confirmed2, reason2 = _model_value_confirmation_passes(sig2)
    assert confirmed2 is True
    assert "model_value_confirmed" in reason2

def test_ask_worsening_resets_confirmation():
    sig1 = make_test_signal(ask=0.50, edge=0.20)
    confirmed1, reason1 = _model_value_confirmation_passes(sig1)
    assert confirmed1 is False
    
    # Ask worsens by 3c (> 2c max worsen)
    sig2 = make_test_signal(ask=0.53, edge=0.17, received_at_ns=sig1.received_at_ns + 2 * 1_000_000_000)
    confirmed2, reason2 = _model_value_confirmation_passes(sig2)
    assert confirmed2 is False
    assert "model_value_confirm_ask_worsened" in reason2
    
    # Check that it armed again on the new state
    key = f"{sig2.match_id}|{sig2.token_id}|{sig2.side}"
    assert _MODEL_VALUE_CONFIRM_STATE[key]["ask"] == 0.53

def test_edge_falling_below_threshold_resets_confirmation():
    sig1 = make_test_signal(ask=0.50, edge=0.20)
    confirmed1, reason1 = _model_value_confirmation_passes(sig1)
    assert confirmed1 is False
    
    # Edge falls below min edge 0.02 (e.g. 0.01)
    sig2 = make_test_signal(ask=0.50, edge=0.01, received_at_ns=sig1.received_at_ns + 2 * 1_000_000_000)
    confirmed2, reason2 = _model_value_confirmation_passes(sig2)
    assert confirmed2 is False
    assert "model_value_confirm_edge_too_low" in reason2
    
    # Should not be armed anymore
    key = f"{sig2.match_id}|{sig2.token_id}|{sig2.side}"
    assert key not in _MODEL_VALUE_CONFIRM_STATE

def test_expiration_resets_confirmation():
    sig1 = make_test_signal(ask=0.50, edge=0.20)
    confirmed1, reason1 = _model_value_confirmation_passes(sig1)
    assert confirmed1 is False
    
    # 95 seconds later (> 90 seconds max age)
    sig2 = make_test_signal(ask=0.50, edge=0.20, received_at_ns=sig1.received_at_ns + 95 * 1_000_000_000)
    confirmed2, reason2 = _model_value_confirmation_passes(sig2)
    assert confirmed2 is False
    assert "model_value_confirm_expired" in reason2
    
    # Armed again with sig2
    key = f"{sig2.match_id}|{sig2.token_id}|{sig2.side}"
    assert _MODEL_VALUE_CONFIRM_STATE[key]["received_at_ns"] == sig2.received_at_ns
