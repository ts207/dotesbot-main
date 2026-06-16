import pytest
from pathlib import Path

def test_no_ml_runtime_terms():
    repo_root = Path(__file__).parent.parent
    banned_terms = [
        "ML_ARBITRAGE",
        "dota_fair_model",
        "load_bundle",
        "build_feature_row",
        "ML_STRATEGY_ENABLED",
        "MIN_ML_EDGE",
        "DOTA_FAIR_MODEL_PATH"
    ]
    
    files_to_check = [
        "main.py", 
        "config.py", 
        "decisive_swing_engine.py", 
        "event_triggered_value_engine.py", 
        "live_exit_engine.py",
        "value_engine.py",
    ]
    
    for filename in files_to_check:
        filepath = repo_root / filename
        if filepath.exists():
            content = filepath.read_text(encoding="utf-8")
            for term in banned_terms:
                assert term not in content, f"Found banned term '{term}' in {filename}"
