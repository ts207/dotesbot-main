from __future__ import annotations

import strategy_registry
from decisive_swing_engine import DSwingSignal
from value_engine import ValueSignal


def test_strategy_registry_loads_event_contracts():
    continuation = strategy_registry.get("EVENT_REPRICE_120")
    reversal = strategy_registry.get("EVENT_REVERSAL_EDGE")

    assert continuation.edge_type == "event_repricing"
    assert continuation.target_horizon == "repricing_120s"
    assert continuation.timeout_sec == 120
    assert reversal.edge_type == "event_overreaction_bounce"
    assert reversal.hold_policy == "reversal_bounce_or_thesis"


def test_value_signal_metadata_comes_from_registry():
    sig = ValueSignal(
        signal_id="s",
        match_id="m",
        received_at_ns=1,
        direction="radiant",
        side="YES",
        token_id="t",
        fair_price=0.7,
        fair_raw=0.72,
        fair_used=0.7,
        model_available=True,
        model_reason="ok",
        ask=0.6,
        edge=0.1,
        lead=3000,
        game_time_sec=700,
        elo_diff=None,
        sized_usd=5,
        book_age_ms=10,
    )

    contract = strategy_registry.get("VALUE_EDGE")
    assert sig.edge_type == contract.edge_type
    assert sig.target_horizon == contract.target_horizon
    assert sig.primary_metric == contract.primary_metric


def test_dswing_signal_metadata_comes_from_registry():
    sig = DSwingSignal(
        signal_id="s",
        match_id="m",
        received_at_ns=1,
        direction="radiant",
        side="YES",
        token_id="t",
        lead=7000,
        game_time_sec=900,
        p_game=0.9,
        p_game_used=0.9,
        series_fair=0.7,
        ask=0.6,
        edge=0.1,
        sized_usd=5,
    )

    contract = strategy_registry.get("DSWING")
    assert sig.edge_type == contract.edge_type
    assert sig.target_horizon == contract.target_horizon
    assert sig.exit_trigger == contract.exit_trigger
