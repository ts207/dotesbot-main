from __future__ import annotations

import time

from actual_dota_event_types import ActualDotaEvent
from event_triggered_value_engine import EventTriggeredValueEngine, EventTriggeredValueSignal


class FakeBookStore(dict):
    pass


def _game(**overrides):
    base = {
        "match_id": "m1",
        "lobby_id": "l1",
        "data_source": "top_live",
        "received_at_ns": time.time_ns(),
        "game_time_sec": 1200,
        "radiant_lead": 7000,
        "radiant_score": 18,
        "dire_score": 11,
        "radiant_team": "Radiant",
        "dire_team": "Dire",
        "building_state": 0x7FF | (0x7FF << 11),
        "tower_state": 0x7FF | (0x7FF << 11),
        "game_over": False,
    }
    base.update(overrides)
    return base


def _event(**overrides):
    base = {
        "event_id": "e1",
        "event_type": "NETWORTH_SWING_WINDOW",
        "match_id": "m1",
        "lobby_id": "l1",
        "league_id": "",
        "source": "top_live",
        "side": "radiant",
        "game_time_sec": 1200,
        "received_at_ns": time.time_ns(),
        "radiant_lead_before": 1000,
        "radiant_lead_after": 7000,
        "radiant_score_before": 10,
        "radiant_score_after": 18,
        "dire_score_before": 10,
        "dire_score_after": 11,
        "networth_delta": 6000,
    }
    base.update(overrides)
    return ActualDotaEvent(**base)


def _mapping(**overrides):
    base = {
        "market_type": "MAP_WINNER",
        "steam_side_mapping": "normal",
        "yes_token_id": "YES",
        "no_token_id": "NO",
        "name": "Radiant vs Dire",
    }
    base.update(overrides)
    return base


def test_event_triggered_value_uses_toplive_event_and_winprob_value():
    engine = EventTriggeredValueEngine()
    books = FakeBookStore(YES={"best_ask": 0.58, "best_bid": 0.56, "received_at_ns": time.time_ns()})

    results = engine.evaluate(
        event=_event(),
        game=_game(),
        mapping=_mapping(),
        book_store=books,
        entered_tokens=set(),
    )

    assert len(results) == 1
    signal = results[0]
    assert isinstance(signal, EventTriggeredValueSignal)
    assert signal.token_id == "YES"
    assert signal.direction == "radiant"
    assert signal.fair_after > signal.fair_before
    assert signal.fair_price == signal.fair_after
    assert signal.fair_delta >= 0.06
    assert signal.edge >= 0.10
    assert signal.to_signal_dict()["event_type"] == "EVENT_CONTINUATION_EDGE"
    assert signal.to_signal_dict()["actual_event_type"] == "NETWORTH_SWING_WINDOW"
    assert signal.to_signal_dict()["fair_after"] == signal.fair_after
    assert signal.to_signal_dict()["hold_policy"] == "thesis_invalidation"


def test_event_triggered_value_rejects_non_toplive_or_game_end():
    engine = EventTriggeredValueEngine()
    books = FakeBookStore(YES={"best_ask": 0.58, "best_bid": 0.56, "received_at_ns": time.time_ns()})

    non_toplive = engine.evaluate(
        event=_event(source="live_league"),
        game=_game(),
        mapping=_mapping(),
        book_store=books,
    )[0]
    assert non_toplive.reason == "not_top_live"

    delayed_game = engine.evaluate(
        event=_event(source="top_live"),
        game=_game(data_source="realtime_stats"),
        mapping=_mapping(),
        book_store=books,
    )[0]
    assert delayed_game.reason == "not_top_live"

    game_end = engine.evaluate(
        event=_event(event_type="GAME_ENDED", side=""),
        game=_game(),
        mapping=_mapping(),
        book_store=books,
    )[0]
    assert game_end.reason == "game_over"


def test_event_triggered_value_rejects_non_primitive_actual_events():
    engine = EventTriggeredValueEngine()
    books = FakeBookStore(YES={"best_ask": 0.58, "best_bid": 0.56, "received_at_ns": time.time_ns()})

    roshan = engine.evaluate(
        event=_event(event_type="ROSHAN_KILLED"),
        game=_game(),
        mapping=_mapping(),
        book_store=books,
    )[0]
    assert roshan.reason == "unsupported_actual_event_type"

    aegis = engine.evaluate(
        event=_event(event_type="AEGIS_PICKED_UP"),
        game=_game(),
        mapping=_mapping(),
        book_store=books,
    )[0]
    assert aegis.reason == "unsupported_actual_event_type"


def test_event_triggered_value_rejects_non_live_grade_multikill():
    engine = EventTriggeredValueEngine()
    books = FakeBookStore(YES={"best_ask": 0.58, "best_bid": 0.56, "received_at_ns": time.time_ns()})

    # live_grade_event = False should be rejected
    rejected = engine.evaluate(
        event=_event(event_type="MULTI_KILL_WINDOW", live_grade_event=False),
        game=_game(),
        mapping=_mapping(),
        book_store=books,
    )[0]
    assert rejected.reason == "multi_kill_not_live_grade"

    # live_grade_event = True (or default) should not be rejected for multi_kill_not_live_grade
    allowed = engine.evaluate(
        event=_event(event_type="MULTI_KILL_WINDOW", live_grade_event=True, side="radiant", radiant_lead_before=1000, radiant_lead_after=1500),
        game=_game(radiant_lead=1500),
        mapping=_mapping(),
        book_store=books,
    )[0]
    assert allowed.reason != "multi_kill_not_live_grade"
