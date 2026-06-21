import pytest
import os
from model_value_predictor import load_model, build_side_features, predict_probability
import model_value_predictor

def test_load_model_valid():
    # Reset model caching first
    model_value_predictor._MODEL_DATA = None
    model_value_predictor._FEATURE_NAMES = None
    model_value_predictor._METADATA = None
    
    loaded = load_model("models/dota_lgbm_win/model.json")
    assert loaded is True
    assert model_value_predictor._MODEL_DATA is not None
    assert model_value_predictor._FEATURE_NAMES == [
      "market_mid",
      "ask",
      "spread",
      "game_time_sec",
      "token_net_worth_lead",
      "token_score_margin",
      "token_net_worth_lead_per_min"
    ]
    assert model_value_predictor._METADATA is not None
    assert model_value_predictor._METADATA.get("strategy") == "MODEL_VALUE_EDGE"

def test_load_model_invalid():
    # Reset model caching first
    model_value_predictor._MODEL_DATA = None
    model_value_predictor._FEATURE_NAMES = None
    model_value_predictor._METADATA = None
    
    loaded = load_model("models/nonexistent/model.json")
    assert loaded is False
    assert model_value_predictor._MODEL_DATA is None

def test_build_side_features():
    game = {
        "radiant_net_worth": 15000,
        "dire_net_worth": 12000,
        "radiant_score": 15,
        "dire_score": 10
    }
    mapping = {}
    
    # Radiant side
    rad_features = build_side_features(game, mapping, "radiant")
    assert rad_features is not None
    assert rad_features["token_net_worth_lead"] == 3000.0
    assert rad_features["token_score_margin"] == 5.0
    
    # Dire side
    dire_features = build_side_features(game, mapping, "dire")
    assert dire_features is not None
    assert dire_features["token_net_worth_lead"] == -3000.0
    assert dire_features["token_score_margin"] == -5.0

    # Missing features returns NaN
    import math
    incomplete_game = {
        "radiant_net_worth": 15000,
        "dire_net_worth": 12000,
        # missing scores
    }
    inc = build_side_features(incomplete_game, mapping, "radiant")
    assert inc is not None
    assert math.isnan(inc["token_score_margin"])

def test_predict_probability():
    # Make sure model is loaded
    load_model("models/dota_lgbm_win/model.json")
    
    # Standard prediction: Radiant leads significantly
    features = {
        "market_mid": 0.5,
        "ask": 0.5,
        "spread": 0.0,
        "game_time_sec": 600,
        "token_net_worth_lead": 6000.0,
        "token_score_margin": 6.0,
        "token_net_worth_lead_per_min": 600.0
    }
    res = predict_probability(features)
    assert res["features_available"] is True
    assert res["reason"] == "ok"
    assert 0.0 <= res["model_probability"] <= 1.0
    assert res["model_probability"] > 0.5

    # Significant disadvantage
    features_disadv = {
        "market_mid": 0.5,
        "ask": 0.5,
        "spread": 0.0,
        "game_time_sec": 600,
        "token_net_worth_lead": -6000.0,
        "token_score_margin": -6.0,
        "token_net_worth_lead_per_min": -600.0
    }
    res_disadv = predict_probability(features_disadv)
    assert res_disadv["features_available"] is True
    assert res_disadv["model_probability"] < 0.5

    # Test missing features
    res_missing = predict_probability({})
    assert res_missing["features_available"] is False
    assert res_missing["reason"] == "missing_required_features"
