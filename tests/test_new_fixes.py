import time
import pytest
from unittest.mock import MagicMock, patch

from paper_trader import PaperTrader, Position
from live_executor import LiveExecutor
from decisive_swing_engine import DecisiveSwingEngine, _series_fair
from event_triggered_value_engine import EventTriggeredValueEngine
from actual_dota_event_types import ActualDotaEvent
import config

# ... existing tests ...

def test_paper_value_hold_does_not_exit_tp_or_trailing():
    trader = PaperTrader()
    trader.positions["tok_1"] = Position(
        token_id="tok_1", match_id="m1", market_name="M",
        side="YES", entry_price=0.50, shares=10, cost_usd=5, entry_time_ns=time.time_ns(),
        entry_game_time_sec=1000, lag=0.0,
        event_type="VALUE_HOLD", strategy_kind="VALUE", hold_policy="thesis_invalidation",
        expected_move=0.0, fair_price=0.80, peak_bid=0.70
    )
    # TP hit (0.76 > 0.5+0.25 TP) or trailing hit
    exits = trader.check_exits({"tok_1": {"best_bid": 0.76, "best_ask": 0.78}}, set(), set(), set())
    assert not exits, f"Should not exit, but exited with: {exits[0].exit_reason if exits else ''}"

def test_paper_value_hold_exits_game_over():
    trader = PaperTrader()
    trader.positions["tok_1"] = Position(
        token_id="tok_1", match_id="m1", market_name="M",
        side="YES", entry_price=0.50, shares=10, cost_usd=5, entry_time_ns=time.time_ns(),
        entry_game_time_sec=1000, lag=0.0,
        event_type="VALUE_HOLD", strategy_kind="VALUE", hold_policy="thesis_invalidation",
        expected_move=0.0, fair_price=0.80, peak_bid=0.50
    )
    exits = trader.check_exits({"tok_1": {"best_bid": 0.50}}, {"m1"}, set(), set())
    assert len(exits) == 1
    assert exits[0].exit_reason == "game_over"

def test_paper_event_triggered_value_uses_value_branch():
    trader = PaperTrader()
    trader.positions["tok_1"] = Position(
        token_id="tok_1", match_id="m1", market_name="M",
        side="YES", entry_price=0.50, shares=10, cost_usd=5, entry_time_ns=time.time_ns(),
        entry_game_time_sec=1000, lag=0.0,
        event_type="EVENT_TRIGGERED_VALUE", strategy_kind="EVENT_TRIGGERED_VALUE", hold_policy="thesis_invalidation",
        expected_move=0.0, fair_price=0.80, peak_bid=0.70
    )
    exits = trader.check_exits({"tok_1": {"best_bid": 0.76}}, set(), set(), set())
    assert not exits, f"Should not exit, but exited with: {exits[0].exit_reason if exits else ''}"
    
    exits = trader.check_exits({"tok_1": {"best_bid": 0.50}}, {"m1"}, set(), set())
    assert len(exits) == 1
    assert exits[0].exit_reason == "game_over"

@pytest.mark.asyncio
@patch("live_executor.validate_mapping_identity")
async def test_try_buy_value_rejects_bad_mapping(mock_validate):
    mock_validate.return_value.ok = False
    mock_validate.return_value.mapping_errors = ["confidence_too_low"]
    
    le = LiveExecutor()
    sig = MagicMock()
    sig.token_id = "tok_yes"
    sig.sized_usd = 5.0
    mapping = {"yes_token_id": "tok_yes", "no_token_id": "tok_no", "dota_match_id": "m1"}
    game = {"match_id": "m2"} # mismatch
    att = await le.try_buy_value(signal=sig, mapping=mapping, game=game, book_store={})
    assert att.order_status == "rejected_precheck"
    assert "mapping_invalid" in att.reason_if_rejected

@pytest.mark.asyncio
@patch("live_executor.validate_mapping_identity")
async def test_try_buy_value_rejects_ask_above_price_cap(mock_validate):
    mock_validate.return_value.ok = True
    
    le = LiveExecutor()
    le._submitted_match_usd = {}
    sig = MagicMock()
    sig.token_id = "tok_yes"
    sig.sized_usd = 5.0
    sig.fair_price = 0.80
    sig.direction = "radiant"
    sig.__class__.__name__ = "ValueSignal"
    mapping = {"yes_token_id": "tok_yes", "no_token_id": "tok_no", "dota_match_id": "m1", "tick_size": 0.01}
    game = {"match_id": "m1", "radiant_team_id": 1, "dire_team_id": 2, "radiant_team": "A", "dire_team": "B"}
    
    book_store = {"tok_yes": {"best_ask": 0.85}}
    
    att = await le.try_buy_value(signal=sig, mapping=mapping, game=game, book_store=book_store)
    assert att.order_status == "rejected_precheck"
    assert "best_ask_above_price_cap" in att.reason_if_rejected

@pytest.mark.asyncio
@patch("live_executor.validate_mapping_identity")
async def test_try_buy_value_rejects_ask_near_fair(mock_validate):
    mock_validate.return_value.ok = True
    
    le = LiveExecutor()
    le._submitted_match_usd = {}
    sig = MagicMock()
    sig.token_id = "tok_yes"
    sig.sized_usd = 5.0
    sig.fair_price = 0.80
    sig.direction = "radiant"
    sig.__class__.__name__ = "ValueSignal"
    mapping = {"yes_token_id": "tok_yes", "no_token_id": "tok_no", "dota_match_id": "m1", "tick_size": 0.01}
    game = {"match_id": "m1", "radiant_team_id": 1, "dire_team_id": 2, "radiant_team": "A", "dire_team": "B"}
    
    book_store = {"tok_yes": {"best_ask": 0.796}}
    att = await le.try_buy_value(signal=sig, mapping=mapping, game=game, book_store=book_store)
    assert att.order_status == "rejected_precheck"
    assert "fresh_ask_not_below_fair" in att.reason_if_rejected

def test_reject_value_labels():
    le = LiveExecutor()
    sig1 = MagicMock()
    sig1.__class__.__name__ = "EventTriggeredValueSignal"
    sig1.is_reversal = False
    sig1.is_continuation = True
    att1 = le._reject_value(sig1, {}, {}, "tok_1", 5.0, "reason")
    assert att1.event_type == "EVENT_CONTINUATION_EDGE"

    sig1_rev = MagicMock()
    sig1_rev.__class__.__name__ = "EventTriggeredValueSignal"
    sig1_rev.is_reversal = True
    sig1_rev.is_continuation = False
    att1_rev = le._reject_value(sig1_rev, {}, {}, "tok_1", 5.0, "reason")
    assert att1_rev.event_type == "EVENT_REVERSAL_EDGE"
    
    sig2 = MagicMock()
    sig2.__class__.__name__ = "DSwingSignal"
    att2 = le._reject_value(sig2, {}, {}, "tok_1", 5.0, "reason")
    assert att2.event_type == "DSWING"

def test_legacy_adverse_exit_disabled():
    assert not config.ENABLE_LEGACY_ADVERSE_EXITS

def test_dswing_rejects_missing_state():
    # Missing current_game_number or series score
    mapping = {"market_type": "MATCH_WINNER", "current_game_number": None}
    res = _series_fair(mapping, "YES", 0.95)
    assert res is None

@patch("decisive_swing_engine.DSWING_ENABLED", True)
def test_dswing_engine_rejects_missing_series_state():
    engine = DecisiveSwingEngine()
    game = {
        "match_id": "m1", "data_source": "top_live", "game_over": False,
        "radiant_team": "A", "dire_team": "B", "radiant_team_id": 1, "dire_team_id": 2,
        "game_time_sec": 1000, "radiant_lead": 10000, # Large lead to pass DSWING_LEAD
        "server_steam_id": "1", "lobby_id": "1",
        "received_at_ns": time.time_ns(),
        "radiant_score": 10, "dire_score": 10,
        "building_state": 1234, "tower_state": 1234
    }
    mapping = {"market_type": "MATCH_WINNER", "yes_token_id": "t1", "current_game_number": None}
    book_store = {"t1": {"best_ask": 0.50, "received_at_ns": time.time_ns()}}
    
    res = engine.evaluate(game, mapping, book_store)
    assert len(res) == 1
    assert res[0].reason == "missing_series_state_or_model"

@patch("event_triggered_value_engine.EVENT_TRIGGERED_VALUE_ENABLED", True)
@patch("event_triggered_value_engine.EVENT_VALUE_REVERSAL_MAX_ASK", 0.45)
@patch("event_triggered_value_engine.EVENT_VALUE_MIN_ASK", 0.50)
def test_event_value_reversal_ask_bounds():
    engine = EventTriggeredValueEngine()
    
    game = {
        "match_id": "m1", "data_source": "top_live", "game_over": False,
        "radiant_team": "A", "dire_team": "B", "radiant_team_id": 1, "dire_team_id": 2,
        "game_time_sec": 1000, "server_steam_id": "1", "lobby_id": "1",
        "received_at_ns": time.time_ns(),
        "radiant_score": 10, "dire_score": 10,
        "building_state": 1234, "tower_state": 1234,
        "radiant_lead": -3000
    }
    
    event = ActualDotaEvent(
        event_id="e1", event_type="TEAM_KILL_SCORE_CHANGE",
        side="radiant", match_id="m1", received_at_ns=time.time_ns(),
        radiant_lead_before=-5000, radiant_lead_after=-3000, # Dire is leading (lead_after < 0), but Radiant got the event -> REVERSAL
        source="top_live",
        lobby_id="1", league_id=1, game_time_sec=1000
    )
    
    mapping = {"market_type": "MAP_WINNER", "yes_token_id": "t1"}
    
    # Test 1: Reversal with ask=0.30 (Valid, below max_ask of 0.45, not blocked by global min of 0.50)
    book_store_valid = {"t1": {"best_ask": 0.30, "received_at_ns": time.time_ns()}}
    res_valid = engine.evaluate(event=event, game=game, mapping=mapping, book_store=book_store_valid)
    assert len(res_valid) == 1
    assert res_valid[0].__class__.__name__ == "EventTriggeredValueReject" # Will likely reject on edge, but importantly NOT on price_too_low
    assert res_valid[0].reason != "price_too_low", "Should not be blocked by global min_ask"
    
    # Test 2: Reversal with ask=0.46 (Invalid, above max_ask of 0.45)
    book_store_high = {"t1": {"best_ask": 0.46, "received_at_ns": time.time_ns()}}
    res_high = engine.evaluate(event=event, game=game, mapping=mapping, book_store=book_store_high)
    assert len(res_high) == 1
    assert res_high[0].reason == "price_too_high"
    
    # Test 3: Continuation with ask=0.49 (Invalid, below global min_ask of 0.50)
    event_cont = ActualDotaEvent(
        event_id="e2", event_type="TEAM_KILL_SCORE_CHANGE",
        side="dire", match_id="m1", received_at_ns=time.time_ns(),
        radiant_lead_before=-3000, radiant_lead_after=-5000, # Dire is leading, Dire got event -> CONTINUATION
        source="top_live",
        lobby_id="1", league_id=1, game_time_sec=1000
    )
    mapping_cont = {"market_type": "MAP_WINNER", "no_token_id": "t2"} # Dire is NO token
    book_store_cont = {"t2": {"best_ask": 0.49, "received_at_ns": time.time_ns()}}
    res_cont = engine.evaluate(event=event_cont, game=game, mapping=mapping_cont, book_store=book_store_cont)
    assert len(res_cont) == 1
    assert res_cont[0].reason == "price_too_low"
