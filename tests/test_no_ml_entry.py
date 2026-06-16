import pytest
from pathlib import Path

def test_main_has_no_dota_fair_model_import():
    text = Path("main.py").read_text(encoding="utf-8")
    assert "from dota_fair_model" not in text
    assert "import dota_fair_model" not in text

def test_ml_arbitrage_path_removed():
    text = Path("main.py").read_text(encoding="utf-8")
    assert "ML_ARBITRAGE" not in text
    assert "build_feature_row" not in text
    assert "load_bundle" not in text
