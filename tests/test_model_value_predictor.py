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
    assert model_value_predictor._FEATURE_NAMES == ["token_net_worth_lead", "token_score_margin"]
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

    # Missing features returns None
    incomplete_game = {
        "radiant_net_worth": 15000,
        "dire_net_worth": 12000,
        # missing scores
    }
    assert build_side_features(incomplete_game, mapping, "radiant") is None

def test_predict_probability():
    # Make sure model is loaded
    load_model("models/dota_lgbm_win/model.json")
    
    # Standard prediction: Radiant leads significantly
    features = {
        "token_net_worth_lead": 6000.0,
        "token_score_margin": 6.0
    }
    res = predict_probability(features)
    assert res["features_available"] is True
    assert res["reason"] == "ok"
    assert 0.0 <= res["model_probability"] <= 1.0
    # Score for NW lead > 5000: 1.5, score for score margin > 5: 0.5. Total score = 2.0. Sigmoid(2.0) > 0.8
    assert res["model_probability"] > 0.8

    # Significant disadvantage
    features_disadv = {
        "token_net_worth_lead": -6000.0,
        "token_score_margin": -6.0
    }
    res_disadv = predict_probability(features_disadv)
    assert res_disadv["features_available"] is True
    # Score for NW lead < -5000: -1.5, score for score margin < -5: -0.5. Total score = -2.0. Sigmoid(-2.0) < 0.2
    assert res_disadv["model_probability"] < 0.2

    # Test missing features
    res_missing = predict_probability({})
    assert res_missing["features_available"] is False
    assert res_missing["reason"] == "missing_required_features"
