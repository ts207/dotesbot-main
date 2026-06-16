import time

import pytest

from event_taxonomy import event_is_primary, event_tier
from signal_engine import EventSignalEngine, MIN_FILL_PRICE, MIN_LAG, apply_suppressions


TOKEN_YES = "YES_TOKEN"
TOKEN_NO = "NO_TOKEN"


def _engine_with_price(token_id: str, price: float) -> EventSignalEngine:
    engine = EventSignalEngine()
    engine.record_price(token_id, price)
    return engine


def _game(now_ns: int, game_time_sec: int = 1200) -> dict:
    # 2026-05-28 — game_time=1200 (20min) sits inside the strong phase windows
    # (skipped phases are <15m, 45-50m, >60m) and matches the original test
    # contract from before the Phase A.2 phase-mask work.
    return {
        "match_id": "M1",
        "received_at_ns": now_ns,
        "game_time_sec": game_time_sec,
        "radiant_team": "team a",
        "dire_team": "team b",
        "radiant_lead": 1500,
        "radiant_score": 12,
        "dire_score": 10,
        "data_source": "top_live",
    }


def _mapping() -> dict:
    return {
        "market_type": "MAP_WINNER",
        "yes_team": "team a",
        "yes_token_id": TOKEN_YES,
        "no_token_id": TOKEN_NO,
        "confidence": 1.0,
    }


def _book(now_ns: int, ask: float = 0.46, bid: float = 0.44, size: float = 100) -> dict:
    return {
        "best_ask": ask,
        "best_bid": bid,
        "ask_size": size,
        "received_at_ns": now_ns,
    }


def _event(event_type="POLL_FIGHT_SWING", direction="radiant", delta=1500):
    # 2026-05-28 — Phase R rolled back the FIGHT_SWING demotion. The 118-match
    # data_v2 validation showed NW-only signals are -$0.10/trade at 46% win;
    # the kill_diff requirement on POLL_FIGHT_SWING was doing useful filtering.
    return {
        "event_type": event_type,
        "direction": direction,
        "delta": delta,
        "severity": "high",
        "game_time_sec": 1800,
        "event_schema_version": "cadence_v1",
        "snapshot_gap_sec": 31,
        "actual_window_sec": 31,
        "networth_delta": delta,
        "kill_diff_delta": 2,
        "total_kills_delta": 2,
        "networth_delta_per_30s": round(delta * 30 / 31, 3),
        "kill_diff_delta_per_30s": round(2 * 30 / 31, 3),
        "source_cadence_quality": "normal",
    }


def test_fires_on_sufficient_lag_and_logs_cadence_metadata():
    # Inject a fair_price_override to clear the edge/lag thresholds; this
    # test guards the cadence-metadata plumbing, not the underlying edge math.
    now_ns = time.time_ns()
    engine = _engine_with_price(TOKEN_YES, 0.45)
    result = engine.evaluate_cluster(
        [_event("POLL_FIGHT_SWING", delta=1800)],
        _game(now_ns),
        _mapping(),
        _book(now_ns, ask=0.42, bid=0.40),
        None,
        fair_price_override=0.62,
        fair_source="hybrid",
    )
    assert result["decision"] == "paper_buy_yes"
    assert result["lag"] > 0
    assert result["event_schema_version"] == "cadence_v1"
    assert result["snapshot_gap_sec"] == 31
    assert result["source_cadence_quality"] == "normal"


def test_retired_fixed_events_are_inactive():
    now_ns = time.time_ns()
    engine = _engine_with_price(TOKEN_YES, 0.45)
    result = engine.evaluate(
        "LEAD_SWING_30S", "radiant", 6000,
        _game(now_ns), _mapping(), _book(now_ns), None,
        severity="high",
    )
    assert result["decision"] == "skip"
    assert result["reason"] == "event_type_inactive"


def test_tactical_events_are_primary_for_clusters():
    # POLL_ULTRA_LATE_FIGHT_FLIP demoted to research 2026-05-26 after signal-quality
    # audit (n=6 signed_60=0c — no measurable edge). POLL_BUYBACK_CAPITULATION is the
    # remaining TIER_A sample (terminal/late-game signal).
    assert event_tier("POLL_BUYBACK_CAPITULATION") == "A"
    assert event_is_primary("POLL_BUYBACK_CAPITULATION") is True
    assert event_tier("POLL_FIGHT_SWING") == "B"
    assert event_is_primary("POLL_FIGHT_SWING") is True
    # 2026-05-28 Phase A.1 — POLL_DECISIVE_STOMP demoted to research after
    # deep_data_study showed n=67, 40% win, -0.79% mean return at +30s.
    assert event_tier("POLL_DECISIVE_STOMP") == "research"
    assert event_is_primary("POLL_DECISIVE_STOMP") is False
    # OBJECTIVE_CONVERSION_T2 was promoted to TIER_B (event_taxonomy.py): 80% end
    # win, 54s reprice lag, mid-game tower kill — now a primary tradeable event.
    assert event_tier("OBJECTIVE_CONVERSION_T2") == "B"
    assert event_is_primary("OBJECTIVE_CONVERSION_T2") is True
    # POLL_KILL_BURST_CONFIRMED + POLL_COMEBACK_RECOVERY promoted 2026-05-26 from
    # research → TIER_B after full-dataset audit showed +2.09c/70% and +2.25c/75%.
    assert event_tier("POLL_KILL_BURST_CONFIRMED") == "B"
    assert event_tier("POLL_COMEBACK_RECOVERY") == "B"
    assert event_tier("LEAD_SWING_30S") == "retired"
    assert event_is_primary("LEAD_SWING_30S") is False


def test_skip_team_side_unknown():
    now_ns = time.time_ns()
    engine = _engine_with_price(TOKEN_YES, 0.45)
    mapping = _mapping()
    mapping["yes_team"] = "team c"
    result = engine.evaluate_cluster([_event()], _game(now_ns), mapping, _book(now_ns), None)
    assert result["decision"] == "skip"
    assert result["reason"] == "team_side_unknown"


def test_skip_fill_price_too_low():
    # The underdog-reversal carve-out (signal_engine ~line 522) lets events in
    # UNDERDOG_REVERSAL_EVENTS (e.g. POLL_FIGHT_SWING) bypass the
    # fill_price_too_low guard when ask is in [MIN_ENTRY, MAX_ENTRY]. To
    # exercise the original guard, drop ask below UNDERDOG_REVERSAL_MIN_ENTRY
    # (default 0.08).
    now_ns = time.time_ns()
    engine = _engine_with_price(TOKEN_YES, 0.05)
    result = engine.evaluate_cluster(
        [_event()],
        _game(now_ns),
        _mapping(),
        _book(now_ns, ask=0.05, bid=0.04),
        None,
    )
    assert result["decision"] == "skip"
    assert result["reason"] == "fill_price_too_low"


@pytest.mark.skip(reason="needs rewrite: T3 was demoted to UNREACHABLE_PRO_EVENTS; the per-event cap mechanism is exercised in test_live_executor balance-gate / cap tests now")
def test_event_specific_fill_cap_blocks_and_allows(monkeypatch):
    # OBJECTIVE_CONVERSION_T3 was demoted to UNREACHABLE_PRO_EVENTS (GG culture
    # in pro Dota means T3 falls almost never trigger). Pin a custom cap on a
    # still-TIER_B event for this mechanism test instead.
    import signal_engine
    monkeypatch.setitem(signal_engine._EVENT_MAX_FILL, "OBJECTIVE_CONVERSION_T2", 0.88)
    now_ns = time.time_ns()
    engine = _engine_with_price(TOKEN_YES, 0.83)
    allowed_event = _event("OBJECTIVE_CONVERSION_T2", delta=1)
    allowed_event["game_time_sec"] = 2400
    allowed = engine.evaluate_cluster(
        [allowed_event],
        _game(now_ns, game_time_sec=2400),
        _mapping(),
        _book(now_ns, ask=0.84, bid=0.82),
        None,
    )
    assert allowed["decision"] == "paper_buy_yes"
    assert allowed["max_fill_price"] == 0.88

    engine = _engine_with_price(TOKEN_YES, 0.83)
    blocked_event = _event("OBJECTIVE_CONVERSION_T2", delta=1)
    blocked_event["game_time_sec"] = 2400
    blocked = engine.evaluate_cluster(
        [blocked_event],
        _game(now_ns, game_time_sec=2400),
        _mapping(),
        _book(now_ns, ask=0.92, bid=0.90),
        None,
    )
    assert blocked["decision"] == "skip"
    assert blocked["reason"] == "fill_price_too_high"


def test_objective_conversion_t2_accepted_as_standalone_primary():
    # OBJECTIVE_CONVERSION_T2 was promoted to TIER_B and is now a primary
    # tradeable event in its own right (event_taxonomy.py). The earlier
    # "skips without primary" behavior no longer applies.
    now_ns = time.time_ns()
    engine = _engine_with_price(TOKEN_YES, 0.45)
    result = engine.evaluate_cluster(
        [_event("OBJECTIVE_CONVERSION_T2", delta=1)],
        _game(now_ns),
        _mapping(),
        _book(now_ns),
        None,
    )
    assert result["decision"] != "skip" or result.get("reason") != "no_primary_event"


def test_suppressions_keep_strongest_cadence_event():
    # SUPPRESSIONS policy (signal_engine.py:77): "POLL_FIGHT_SWING" suppresses
    # "POLL_KILL_BURST_CONFIRMED" because fight_swing is the broader signal.
    # When both fire in the same cluster, fight_swing wins.
    events = [
        {"event_type": "POLL_FIGHT_SWING", "direction": "radiant"},
        {"event_type": "POLL_KILL_BURST_CONFIRMED", "direction": "radiant"},
    ]
    kept = apply_suppressions(events)
    assert [e["event_type"] for e in kept] == ["POLL_FIGHT_SWING"]


def test_cooldown_blocks_second_signal():
    # fair_price_override clears the edge/lag thresholds; this test asserts
    # cooldown blocking on the second call, not edge math on the first.
    now_ns = time.time_ns()
    engine = _engine_with_price(TOKEN_YES, 0.45)
    kwargs = dict(
        events=[_event()],
        game=_game(now_ns),
        mapping=_mapping(),
        yes_book=_book(now_ns, ask=0.42, bid=0.40),
        no_book=None,
        fair_price_override=0.62,
        fair_source="hybrid",
    )
    first = engine.evaluate_cluster(**kwargs)
    assert first["decision"] == "paper_buy_yes"
    engine.commit_signal(first)
    second = engine.evaluate_cluster(**kwargs)
    assert second["decision"] == "skip"
    assert second["reason"] == "cooldown"


def test_source_and_book_freshness_guards():
    now_ns = time.time_ns()
    engine = _engine_with_price(TOKEN_YES, 0.45)
    game = _game(now_ns)
    game["data_source"] = "live_league"
    result = engine.evaluate_cluster([_event()], game, _mapping(), _book(now_ns), None)
    assert result["decision"] == "skip"
    assert result["reason"] == "non_top_live_source"

    stale_ns = now_ns - 10_000_000_000
    engine = _engine_with_price(TOKEN_YES, 0.45)
    result = engine.evaluate_cluster([_event()], _game(now_ns), _mapping(), _book(stale_ns), None)
    assert result["decision"] == "skip"
    assert result["reason"] == "book_stale"
    assert result["event_tier"] == "B"
    assert result["token_id"] == TOKEN_YES


def test_already_repriced_skip_keeps_side_metadata():
    now_ns = time.time_ns()
    engine = EventSignalEngine()
    engine.record_price(TOKEN_YES, 0.45)
    # Move the anchor back to 6s ago (instead of 31s) to match the new 5s repricing check.
    engine._price_history[TOKEN_YES][0] = (int(time.time() * 1000) - 6_000, 0.45)
    engine.record_price(TOKEN_YES, 0.58)  # Move > 0.12 (1.5 * 0.08)

    result = engine.evaluate_cluster(
        [_event()],
        _game(now_ns),
        _mapping(),
        _book(now_ns, ask=0.59, bid=0.57),
        None,
    )

    assert result["decision"] == "skip"
    assert result["reason"] == "already_repriced"
    assert result["token_id"] == TOKEN_YES
    assert result["side"] == "YES"


def test_hybrid_fair_override_drives_edge_gate():
    now_ns = time.time_ns()
    engine = _engine_with_price(TOKEN_YES, 0.45)
    result = engine.evaluate_cluster(
        events=[_event()],
        game=_game(now_ns),
        mapping=_mapping(),
        yes_book=_book(now_ns, ask=0.46, bid=0.44),
        no_book=None,
        fair_price_override=0.62,
        fair_source="hybrid",
    )
    assert result["decision"] == "paper_buy_yes"
    # Contract: the override drives the decision; the engine may or may not
    # top it up depending on the event's expected_move cap. Asserting fair_price
    # >= override is enough to guard the gate behavior.
    assert result["fair_price"] >= 0.62
    assert result["fair_source"] == "hybrid"

def test_structure_confidence_lowers_impact():
    now_ns = time.time_ns()
    engine = _engine_with_price(TOKEN_YES, 0.45)
    
    event_high = _event("OBJECTIVE_CONVERSION_T3", delta=1)
    event_high["structure_confidence"] = 1.0
    
    res_high = engine.evaluate_cluster(
        events=[event_high],
        game=_game(now_ns),
        mapping=_mapping(),
        yes_book=_book(now_ns),
        no_book=None,
        require_primary=False
    )
    
    event_low = _event("OBJECTIVE_CONVERSION_T3", delta=1)
    event_low["structure_confidence"] = 0.5
    
    res_low = engine.evaluate_cluster(
        events=[event_low],
        game=_game(now_ns),
        mapping=_mapping(),
        yes_book=_book(now_ns),
        no_book=None,
        require_primary=False
    )
    
    assert res_low["expected_move"] < res_high["expected_move"]
    # Penalty should be logged
    assert res_low["structure_uncertainty_penalty"] > res_high["structure_uncertainty_penalty"]
    # Required edge should be higher
    assert res_low["required_edge"] > res_high["required_edge"]
