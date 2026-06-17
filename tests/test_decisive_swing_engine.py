import pytest
from unittest.mock import MagicMock, patch
from decisive_swing_engine import DecisiveSwingEngine, DSwingReject, DSwingSignal, _series_fair

def test_dswing_series_fair_uses_next_map_probability():
    mapping = {
        "current_game_number": "1",
        "series_score_yes": "0",
        "series_score_no": "0"
    }
    fair = _series_fair(mapping, "YES", p_game=0.9, p_next_side=0.8)
    assert fair is not None
    assert abs(fair - 0.928) < 0.001

@patch("time.time_ns")
@patch("winprob.fair")
@patch("fair_value.compute_side_fair")
def test_dswing_reject_price_too_high_includes_context_fields(mock_compute, mock_winprob, mock_time_ns):
    mock_time_ns.return_value = 1000000000
    mock_fair_res = MagicMock()
    mock_fair_res.model_available = True
    mock_fair_res.fair_used = 0.90
    mock_fair_res.elo_diff = 100
    mock_fair_res.draft_h2h = 0
    mock_compute.return_value = mock_fair_res
    mock_winprob.return_value = 0.6
    
    engine = DecisiveSwingEngine()
    from datetime import datetime, timezone
    game = {"match_id": "123", "data_source": "top_live", "radiant_score": 10, "dire_score": 5, "radiant_lead": 7000, "game_time_sec": 900, "building_state": 0, "tower_state": 0, "received_at_ns": int(datetime.now(timezone.utc).timestamp() * 1e9)}
    mapping = {
        "market_type": "MATCH_WINNER",
        "steam_side_mapping": "normal",
        "yes_token_id": "tok1",
        "current_game_number": 1,
        "series_score_yes": 0,
        "series_score_no": 0
    }
    book_store = MagicMock()
    from datetime import datetime, timezone
    book_store.get.return_value = {"best_ask": 0.95, "received_at_ns": 1000000000}
    
    res = list(engine.evaluate(game, mapping, book_store))
    assert len(res) == 1
    rej = res[0]
    assert isinstance(rej, DSwingReject)
    assert rej.reason == "price_too_high"
    assert rej.ask == 0.95
    assert rej.direction == "radiant"
    assert rej.side == "YES"
    assert rej.token_id == "tok1"

@patch("fair_value.compute_side_fair")
def test_dswing_reject_missing_ask_includes_match_side_token_context(mock_compute):
    engine = DecisiveSwingEngine()
    from datetime import datetime, timezone
    game = {"match_id": "123", "data_source": "top_live", "radiant_score": 10, "dire_score": 5, "radiant_lead": 7000, "game_time_sec": 900, "building_state": 0, "tower_state": 0, "received_at_ns": int(datetime.now(timezone.utc).timestamp() * 1e9)}
    mapping = {
        "market_type": "MATCH_WINNER",
        "steam_side_mapping": "normal",
        "yes_token_id": "tok1",
    }
    book_store = MagicMock()
    book_store.get.return_value = {}
    
    res = list(engine.evaluate(game, mapping, book_store))
    assert len(res) == 1
    rej = res[0]
    assert isinstance(rej, DSwingReject)
    assert rej.reason == "missing_ask"
    assert rej.direction == "radiant"
    assert rej.side == "YES"
    assert rej.token_id == "tok1"

@patch("time.time_ns")
@patch("winprob.fair")
@patch("fair_value.compute_side_fair")
def test_dswing_reject_edge_too_small_includes_p_game_and_series_fair(mock_compute, mock_winprob, mock_time_ns):
    mock_time_ns.return_value = 1000000000
    mock_fair_res = MagicMock()
    mock_fair_res.model_available = True
    mock_fair_res.fair_used = 0.90
    mock_fair_res.elo_diff = 100
    mock_fair_res.draft_h2h = 0
    mock_compute.return_value = mock_fair_res
    mock_winprob.return_value = 0.6
    
    engine = DecisiveSwingEngine()
    from datetime import datetime, timezone
    game = {"match_id": "123", "data_source": "top_live", "radiant_score": 10, "dire_score": 5, "radiant_lead": 7000, "game_time_sec": 900, "building_state": 0, "tower_state": 0, "received_at_ns": int(datetime.now(timezone.utc).timestamp() * 1e9)}
    mapping = {
        "market_type": "MATCH_WINNER",
        "steam_side_mapping": "normal",
        "yes_token_id": "tok1",
        "current_game_number": 1,
        "series_score_yes": 0,
        "series_score_no": 0
    }
    book_store = MagicMock()
    from datetime import datetime, timezone
    book_store.get.return_value = {"best_ask": 0.78, "received_at_ns": 1000000000}
    
    res = list(engine.evaluate(game, mapping, book_store))
    assert len(res) == 1
    rej = res[0]
    assert isinstance(rej, DSwingReject)
    assert rej.reason.startswith("edge_too_small")
    assert rej.p_game == 0.90
    assert abs(rej.series_fair - 0.792) < 0.001
import pytest
from unittest.mock import MagicMock, patch
from decisive_swing_engine import DecisiveSwingEngine, DSwingReject

def test_dswing_wrong_market_type_returns_reject():
    engine = DecisiveSwingEngine()
    res = list(engine.evaluate({"match_id": "123"}, {"market_type": "MAP_WINNER"}, None))
    assert len(res) == 1
    assert isinstance(res[0], DSwingReject)
    assert res[0].reason == "wrong_market_type"

def test_dswing_invalid_top_live_state_returns_reject():
    engine = DecisiveSwingEngine()
    game = {"match_id": "123", "data_source": "top_live"}
    mapping = {"market_type": "MATCH_WINNER"}
    res = list(engine.evaluate(game, mapping, None))
    assert len(res) == 1
    assert isinstance(res[0], DSwingReject)
    assert res[0].reason == "invalid_top_live_state"

@patch("decisive_swing_engine.validate_top_live_state")
def test_dswing_game_too_early_returns_reject(mock_val):
    mock_val.return_value.ok = True
    engine = DecisiveSwingEngine()
    game = {"match_id": "123", "data_source": "top_live", "received_at_ns": 1, "game_time_sec": 100}
    mapping = {"market_type": "MATCH_WINNER"}
    res = list(engine.evaluate(game, mapping, None))
    assert len(res) == 1
    assert isinstance(res[0], DSwingReject)
    assert res[0].reason == "game_too_early"

@patch("decisive_swing_engine.validate_top_live_state")
def test_dswing_missing_lead_returns_reject(mock_val):
    mock_val.return_value.ok = True
    engine = DecisiveSwingEngine()
    game = {"match_id": "123", "data_source": "top_live", "received_at_ns": 1, "game_time_sec": 900}
    mapping = {"market_type": "MATCH_WINNER"}
    res = list(engine.evaluate(game, mapping, None))
    assert len(res) == 1
    assert isinstance(res[0], DSwingReject)
    assert res[0].reason == "missing_lead"

@patch("decisive_swing_engine.validate_top_live_state")
def test_dswing_lead_too_small_returns_reject(mock_val):
    mock_val.return_value.ok = True
    engine = DecisiveSwingEngine()
    game = {"match_id": "123", "data_source": "top_live", "received_at_ns": 1, "game_time_sec": 900, "radiant_lead": 1000}
    mapping = {"market_type": "MATCH_WINNER"}
    res = list(engine.evaluate(game, mapping, None))
    assert len(res) == 1
    assert isinstance(res[0], DSwingReject)
    assert res[0].reason == "lead_too_small"
