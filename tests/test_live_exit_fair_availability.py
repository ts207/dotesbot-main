import pytest
from unittest.mock import MagicMock
from live_exit_engine import _current_fair_for_position

def test_current_fair_for_position_respects_model_availability(monkeypatch):
    pos = MagicMock()
    pos.backed_direction = "radiant"
    game = {"radiant_lead": 1000, "game_time_sec": 600}
    
    # Mock compute_side_fair to return model_available=False
    class FakeFairRes:
        model_available = False
        fair_used = 0.5
        fair = 0.5
        
    monkeypatch.setattr("live_exit_engine.compute_side_fair", lambda **kwargs: FakeFairRes())
    
    fair = _current_fair_for_position(pos, game)
    assert fair is None

def test_current_fair_for_position_returns_fair_when_available(monkeypatch):
    pos = MagicMock()
    pos.backed_direction = "radiant"
    game = {"radiant_lead": 1000, "game_time_sec": 600}
    
    class FakeFairRes:
        model_available = True
        fair_used = 0.85
        fair = 0.85
        
    monkeypatch.setattr("live_exit_engine.compute_side_fair", lambda **kwargs: FakeFairRes())
    
    fair = _current_fair_for_position(pos, game)
    assert fair == 0.85
