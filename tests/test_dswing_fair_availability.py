import pytest
from unittest.mock import MagicMock
from decisive_swing_engine import DecisiveSwingEngine
from poly_ws import BookStore

def test_dswing_rejects_when_model_unavailable(monkeypatch):
    import decisive_swing_engine
    monkeypatch.setattr(decisive_swing_engine, "DSWING_ENABLED", True)
    engine = DecisiveSwingEngine()
    game = {
        "match_id": "m1", 
        "radiant_lead": 10000, 
        "game_time_sec": 3000, 
        "data_source": "top_live",
        "received_at_ns": 12345,
        "radiant_score": 10,
        "dire_score": 5,
        "building_state": 1234,
        "tower_state": 1234,
    }
    mapping = {
        "yes_token_id": "T_YES",
        "no_token_id": "T_NO",
        "market_type": "MATCH_WINNER"
    }
    book_store = BookStore()
    book_store.update_direct("T_YES", best_bid=0.50, best_ask=0.55)
    
    # Mock compute_side_fair to return model_available=False
    class FakeFairRes:
        model_available = False
        model_reason = "missing_data"
        fair_used = 0.99
        fair = 0.99
        
    monkeypatch.setattr("decisive_swing_engine.compute_side_fair", lambda **kwargs: FakeFairRes(), raising=False)
    monkeypatch.setattr("fair_value.compute_side_fair", lambda **kwargs: FakeFairRes(), raising=False)
    
    results = engine.evaluate(game, mapping, book_store)
    assert len(results) == 1
    # Should be rejected because model is unavailable
    assert getattr(results[0], "reason", "").startswith("model_unavailable")
