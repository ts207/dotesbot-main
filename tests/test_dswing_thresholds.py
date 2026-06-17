import pytest
from unittest.mock import MagicMock, patch
from decisive_swing_engine import DecisiveSwingEngine, DSwingReject

@patch("decisive_swing_engine.validate_top_live_state")
def test_dswing_threshold_reduction_yes_team(mock_val):
    mock_val.return_value.ok = True
    engine = DecisiveSwingEngine()
    
    # 6000 is default DSWING_LEAD. 30% reduction is 4200.
    # We provide a lead of 4500, which is normally rejected, but should pass lead check here.
    game = {
        "match_id": "123", 
        "data_source": "top_live", 
        "received_at_ns": 1, 
        "game_time_sec": 900, 
        "radiant_lead": 4500,
        "game_over": False
    }
    
    # Radiant is leading, so direction is radiant. 
    # Let's say yes_team is radiant (normal mapping), so they are up 1-0.
    mapping = {
        "market_type": "MATCH_WINNER",
        "steam_side_mapping": "normal",
        "series_score_yes": 1,
        "series_score_no": 0
    }
    
    res = list(engine.evaluate(game, mapping, None))
    assert len(res) == 1
    # Since it passes the lead check, it should fail on a subsequent check, e.g. "missing_token_id"
    # because yes_token_id is not in mapping. If the threshold logic didn't work,
    # it would return "lead_too_small".
    assert isinstance(res[0], DSwingReject)
    assert res[0].reason != "lead_too_small", "Threshold was not reduced!"
    assert res[0].reason == "missing_token_id"

@patch("decisive_swing_engine.validate_top_live_state")
def test_dswing_threshold_reduction_no_team(mock_val):
    mock_val.return_value.ok = True
    engine = DecisiveSwingEngine()
    
    # Dire is leading (lead < 0). 4500 < 6000.
    game = {
        "match_id": "123", 
        "data_source": "top_live", 
        "received_at_ns": 1, 
        "game_time_sec": 900, 
        "radiant_lead": -4500,
        "game_over": False
    }
    
    # Dire is leading. Let's say dire is NO team (normal mapping). They are up 1-0.
    mapping = {
        "market_type": "MATCH_WINNER",
        "steam_side_mapping": "normal",
        "series_score_yes": 0,
        "series_score_no": 1
    }
    
    res = list(engine.evaluate(game, mapping, None))
    assert len(res) == 1
    assert isinstance(res[0], DSwingReject)
    assert res[0].reason != "lead_too_small", "Threshold was not reduced!"
    assert res[0].reason == "missing_token_id"

@patch("decisive_swing_engine.validate_top_live_state")
def test_dswing_no_threshold_reduction_when_tied(mock_val):
    mock_val.return_value.ok = True
    engine = DecisiveSwingEngine()
    
    # Radiant is leading.
    game = {
        "match_id": "123", 
        "data_source": "top_live", 
        "received_at_ns": 1, 
        "game_time_sec": 900, 
        "radiant_lead": 4500,
        "game_over": False
    }
    
    # Tied 1-1.
    mapping = {
        "market_type": "MATCH_WINNER",
        "steam_side_mapping": "normal",
        "series_score_yes": 1,
        "series_score_no": 1
    }
    
    res = list(engine.evaluate(game, mapping, None))
    assert len(res) == 1
    assert isinstance(res[0], DSwingReject)
    # Should be rejected because it doesn't meet the normal 6000 threshold
    assert res[0].reason == "lead_too_small"
