import pytest
from fair_value import compute_side_fair, _lead_hist

def test_missing_or_invalid_lead_returns_model_unavailable():
    game = {"game_time_sec": 1000}
    res = compute_side_fair(game=game, side="radiant")
    assert not res.model_available
    assert res.model_reason == "missing_radiant_lead"
    assert res.fair == 0.5

    game = {"radiant_lead": "abc", "game_time_sec": 1000}
    res = compute_side_fair(game=game, side="radiant")
    assert not res.model_available
    assert res.model_reason == "invalid_radiant_lead"

def test_missing_or_invalid_time_returns_model_unavailable():
    game = {"radiant_lead": 1000}
    res = compute_side_fair(game=game, side="radiant")
    assert not res.model_available
    assert res.model_reason == "missing_game_time"

    game = {"radiant_lead": 1000, "game_time_sec": "abc"}
    res = compute_side_fair(game=game, side="radiant")
    assert not res.model_available
    assert res.model_reason == "invalid_game_time"

def test_record_history_false_does_not_mutate():
    _lead_hist.clear()
    game = {"match_id": "m1", "radiant_lead": 1000, "game_time_sec": 1000}
    
    # Should not create key
    res = compute_side_fair(game=game, side="radiant", record_history=False)
    assert "m1" not in _lead_hist
    assert res.model_available
    assert not res.slope_available
    assert res.fair_used == res.fair

def test_conservative_fair_shrink():
    game = {"radiant_lead": 10000, "game_time_sec": 1200} # 20 mins, phase 1.0
    res1 = compute_side_fair(game=game, side="radiant", record_history=False)
    assert res1.phase_shrink == 1.0
    assert res1.fair_used == res1.fair_raw

    game2 = {"radiant_lead": 10000, "game_time_sec": 3000} # 50 mins, phase 0.65
    res2 = compute_side_fair(game=game2, side="radiant", record_history=False)
    assert res2.phase_shrink == 0.65
    assert abs(res2.fair_used - 0.5) < abs(res2.fair_raw - 0.5)
