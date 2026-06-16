import time
import pytest
from unittest.mock import MagicMock, patch

from paper_trader import PaperTrader, Position
from live_executor import LiveExecutor
from decisive_swing_engine import DecisiveSwingEngine, _series_fair
import config

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
    att1 = le._reject_value(sig1, {}, {}, "tok_1", 5.0, "reason")
    assert att1.event_type == "EVENT_TRIGGERED_VALUE"
    
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
