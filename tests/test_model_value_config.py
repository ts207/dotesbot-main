import os
from importlib import reload

def test_residual_threshold_default(monkeypatch):
    monkeypatch.delenv("MODEL_VALUE_MIN_EDGE", raising=False)
    monkeypatch.delenv("MODEL_VALUE_CONFIRM_MIN_EDGE", raising=False)
    
    # Test residual mode
    monkeypatch.setenv("MODEL_VALUE_EDGE_MODE", "residual")
    import config
    config = reload(config)
    assert config.MODEL_VALUE_MIN_EDGE == 0.02
    assert config.MODEL_VALUE_CONFIRM_MIN_EDGE == 0.02
    
    # Test non-residual mode
    monkeypatch.setenv("MODEL_VALUE_EDGE_MODE", "legacy")
    config = reload(config)
    assert config.MODEL_VALUE_MIN_EDGE == 0.15
    assert config.MODEL_VALUE_CONFIRM_MIN_EDGE == 0.15
