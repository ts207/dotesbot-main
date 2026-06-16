import pytest
from decisive_swing_engine import DecisiveSwingEngine, _sniped, _load_snipes, _save_snipe, _SNIPES_FILE
import os
import json
import time

def test_dswing_snipe_specificity(monkeypatch, tmp_path):
    # Setup temp snipes file
    import decisive_swing_engine
    monkeypatch.setattr(decisive_swing_engine, "DSWING_ENABLED", True)
    snipes_file = tmp_path / "dswing_snipes.json"
    monkeypatch.setattr(decisive_swing_engine, "_SNIPES_FILE", str(snipes_file))
    decisive_swing_engine._sniped.clear()
    
    match_id = "match1"
    direction = "radiant"
    token_id = "tok1"
    gn = 1
    
    engine = DecisiveSwingEngine()
    
    # Mock compute_bo3_match_p
    import decisive_swing_engine
    monkeypatch.setattr(decisive_swing_engine, "compute_bo3_match_p", lambda *args: 0.9)
    
    # Mock compute_side_fair
    import fair_value
    from fair_value import FairValueResult
    monkeypatch.setattr(fair_value, "compute_side_fair", lambda **kwargs: FairValueResult(side="radiant", fair=0.95, elo_diff=0.0, lead_slope=1.0))

    game = {
        "match_id": match_id,
        "data_source": "top_live",
        "game_time_sec": 700,
        "radiant_lead": 6000,
        "received_at_ns": 12345,
        "radiant_score": 10,
        "dire_score": 5,
        "building_state": 0,
        "tower_state": 0,
    }
    mapping = {
        "market_type": "MATCH_WINNER",
        "yes_token_id": token_id,
        "current_game_number": gn,
        "series_score_yes": 0,
        "series_score_no": 0
    }
    
    class MockBookStore:
        def get(self, tid):
            return {"best_ask": 0.8, "received_at_ns": time.time_ns()}
            
    # First evaluate should succeed and save
    sigs = engine.evaluate(game, mapping, MockBookStore())
    print(f"SIGS: {sigs}")
    import decisive_swing_engine
    print(f"SNIPED: {decisive_swing_engine._sniped}")
    assert len(sigs) == 1
    
    # Check that it's in _sniped
    key = (str(match_id), str(direction), str(token_id), str(gn))
    import decisive_swing_engine
    assert key in decisive_swing_engine._sniped
    
    # Second evaluate with SAME key should return []
    sigs2 = engine.evaluate(game, mapping, MockBookStore())
    assert len(sigs2) == 0
    
    # Evaluate with DIFFERENT gn should succeed
    mapping2 = mapping.copy()
    mapping2["current_game_number"] = 2
    sigs3 = engine.evaluate(game, mapping2, MockBookStore())
    assert len(sigs3) == 1
    
    # Evaluate with DIFFERENT token_id (reversed mapping) should succeed
    mapping3 = mapping.copy()
    mapping3["steam_side_mapping"] = "reversed"
    mapping3["no_token_id"] = "tok2"
    sigs4 = engine.evaluate(game, mapping3, MockBookStore())
    assert len(sigs4) == 1

def test_dswing_snipe_backwards_compatibility(monkeypatch, tmp_path):
    snipes_file = tmp_path / "dswing_snipes_compat.json"
    # Write old style snipes (2-tuples)
    with open(snipes_file, "w") as f:
        json.dump([["m1", "radiant"], ["m2", "dire"]], f)
    
    monkeypatch.setattr("decisive_swing_engine._SNIPES_FILE", str(snipes_file))
    monkeypatch.setattr("decisive_swing_engine._sniped", set())
    
    from decisive_swing_engine import _load_snipes
    _load_snipes()
    
    from decisive_swing_engine import _sniped
    assert ("m1", "radiant", "unknown", "unknown") in _sniped
    assert ("m2", "dire", "unknown", "unknown") in _sniped
    assert len(_sniped) == 2
