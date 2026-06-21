import pytest
from unittest.mock import MagicMock, patch
import time

from model_value_engine import ModelValueEngine, ModelValueSignal

def test_engine_policy_input_has_strategy_family():
    engine = ModelValueEngine()
    
    base_game = {
        "match_id": "m1",
        "data_source": "top_live",
        "radiant_team": "team1",
        "dire_team": "team2",
        "game_over": False,
        "radiant_net_worth": 10000,
        "dire_net_worth": 10000,
        "radiant_lead": 0,
        "radiant_score": 10,
        "dire_score": 10,
        "buildings": [],
        "game_time_sec": 600,
        "received_at_ns": time.time_ns(),
    }
    base_mapping = {
        "match_id": "m1",
        "market_id": "mk1",
        "yes_token_id": "tok1",
        "no_token_id": "tok2",
        "market_type": "MAP_WINNER",
        "steam_side_mapping": "normal",
    }
    
    book_store = MagicMock()
    book_store.get.return_value = {
        "best_ask": 0.50,
        "best_bid": 0.45,
        "received_at_ns": time.time_ns()
    }
    
    with patch("model_value_engine.evaluate_policy") as mock_evaluate_policy, \
         patch("model_value_engine.signal_policy_fields") as mock_spf, \
         patch("model_value_engine.validate_top_live_state") as mock_vtls, \
         patch("model_value_predictor.predict_probability") as mock_pred:
             
        from gettoplive_state import TopLiveStateCheck
        mock_vtls.return_value = TopLiveStateCheck(ok=True, reason="")
        
        mock_pred.return_value = {
            "model_probability": 0.90, # gives huge edge > 0.02
            "model_version": "test",
            "features_available": True,
            "reason": "ok"
        }
        
        mock_spf.return_value = {
            "policy_allowed": True,
            "policy_reason": "ok",
            "policy_version": "v1",
            "risk_tags": "",
            "live_skip_reason": ""
        }
        
        mock_evaluate_policy.return_value = MagicMock() # not actually used directly due to mock_spf
        
        res = engine.evaluate(base_game, base_mapping, book_store, entered_tokens=set())
        signals = [r for r in res if isinstance(r, ModelValueSignal)]
        assert len(signals) > 0
        
        # Verify evaluate_policy was called with the correct PolicyInput
        assert mock_evaluate_policy.call_count >= 1
        policy_input = mock_evaluate_policy.call_args[0][0]
        
        assert policy_input.signal.get("strategy_family") == "MODEL_VALUE"
        assert policy_input.signal.get("strategy_family") == "MODEL_VALUE"
