from __future__ import annotations

import time

from gettoplive_state import validate_top_live_state
from value_engine import ValueEngine, ValueReject


def complete_game() -> dict:
    return {
        "data_source": "top_live",
        "received_at_ns": 123,
        "match_id": "m1",
        "game_time_sec": 700,
        "radiant_lead": 5000,
        "radiant_score": 12,
        "dire_score": 8,
        "building_state": 2047,
        "tower_state": 2047,
    }


def test_validate_top_live_state_passes_complete_gettoplive_snapshot():
    result = validate_top_live_state(complete_game())

    assert result.ok is True
    assert result.reason == "ok"


def test_validate_top_live_state_rejects_slow_source():
    game = complete_game()
    game["data_source"] = "live_league"

    result = validate_top_live_state(game)

    assert result.ok is False
    assert result.reason == "not_top_live"


def test_validate_top_live_state_requires_building_and_tower_state():
    game = complete_game()
    game["building_state"] = ""
    game.pop("tower_state")

    result = validate_top_live_state(game)

    assert result.ok is False
    assert result.reason == "missing_top_live_state"
    assert result.missing_fields == ("building_state", "tower_state")


def test_value_engine_rejects_degraded_top_live_before_book_lookup():
    game = complete_game()
    game.pop("building_state")

    result = ValueEngine().evaluate(
        game,
        mapping={"market_type": "MAP_WINNER", "steam_side_mapping": "normal", "yes_token_id": "Y", "no_token_id": "N"},
        book_store={},
    )

    assert len(result) == 1
    assert isinstance(result[0], ValueReject)
    assert result[0].reason == "missing_top_live_state:building_state"


def test_value_engine_labels_bid_only_book_as_one_sided_missing_ask():
    result = ValueEngine().evaluate(
        complete_game(),
        mapping={"market_type": "MAP_WINNER", "steam_side_mapping": "normal", "yes_token_id": "Y", "no_token_id": "N"},
        book_store={"Y": {"best_bid": 0.99, "best_ask": None, "received_at_ns": time.time_ns() - 100_000_000}},
    )

    assert len(result) == 1
    assert isinstance(result[0], ValueReject)
    assert result[0].reason == "one_sided_book_missing_ask"
    assert result[0].book_age_ms is not None
