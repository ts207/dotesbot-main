import pytest
import time
from unittest.mock import MagicMock, patch
from model_value_engine import ModelValueEngine, ModelValueSignal, ModelValueReject

@pytest.fixture(autouse=True)
def enable_model_value(monkeypatch):
    import model_value_engine
    monkeypatch.setattr(model_value_engine, "MODEL_VALUE_ENABLED", True)


@pytest.fixture
def base_game():
    return {
        "match_id": "123",
        "data_source": "top_live",
        "game_time_sec": 600,
        "radiant_net_worth": 10000,
        "dire_net_worth": 10000,
        "radiant_score": 10,
        "dire_score": 10,
        "radiant_lead": 0,
        "building_state": 0,
        "tower_state": 0,
        "received_at_ns": time.time_ns(),
        "game_over": False
    }

@pytest.fixture
def base_mapping():
    return {
        "market_type": "MAP_WINNER",
        "steam_side_mapping": "normal",
        "yes_token_id": "tok_rad",
        "no_token_id": "tok_dire",
        "name": "Game 1 Winner"
    }

def test_engine_rejects_missing_match_id():
    engine = ModelValueEngine()
    res = engine.evaluate({"data_source": "top_live"}, {}, None, entered_tokens=set())
    assert len(res) == 0

def test_engine_rejects_non_top_live(base_game, base_mapping):
    game = dict(base_game)
    game["data_source"] = "league_stream"
    engine = ModelValueEngine()
    res = engine.evaluate(game, base_mapping, None, entered_tokens=set())
    assert len(res) == 0

def test_engine_rejects_game_over(base_game, base_mapping):
    game = dict(base_game)
    game["game_over"] = True
    engine = ModelValueEngine()
    res = engine.evaluate(game, base_mapping, None, entered_tokens=set())
    assert len(res) == 1
    assert isinstance(res[0], ModelValueReject)
    assert res[0].reason == "game_over"

def test_engine_rejects_missing_book(base_game, base_mapping):
    engine = ModelValueEngine()
    book_store = MagicMock()
    book_store.get.return_value = None
    res = engine.evaluate(base_game, base_mapping, book_store, entered_tokens=set())
    assert len(res) >= 1
    assert any(isinstance(r, ModelValueReject) and r.reason == "missing_book" for r in res)

def test_engine_rejects_stale_book(base_game, base_mapping):
    engine = ModelValueEngine()
    book_store = MagicMock()
    # 20 seconds ago
    book_store.get.return_value = {
        "best_ask": 0.50,
        "best_bid": 0.48,
        "received_at_ns": time.time_ns() - 20 * 1_000_000_000
    }
    with patch("model_value_predictor.predict_probability") as mock_pred:
        mock_pred.return_value = {
            "model_probability": 0.80,
            "model_version": "test",
            "features_available": True,
            "reason": "ok"
        }
        res = engine.evaluate(base_game, base_mapping, book_store, entered_tokens=set())
        assert len(res) >= 1
        # Should reject with book_stale
        assert any(isinstance(r, ModelValueReject) and r.reason == "book_stale" for r in res)

def test_engine_rejects_future_book_timestamp(base_game, base_mapping):
    engine = ModelValueEngine()
    now_ns = time.time_ns()
    game = dict(base_game)
    game["received_at_ns"] = now_ns
    book_store = MagicMock()
    book_store.get.return_value = {
        "best_ask": 0.50,
        "best_bid": 0.48,
        "received_at_ns": now_ns + 1_000_000_000,
    }
    with patch("model_value_predictor.predict_probability") as mock_pred:
        mock_pred.return_value = {
            "model_probability": 0.80,
            "model_version": "test",
            "features_available": True,
            "reason": "ok"
        }
        res = engine.evaluate(game, base_mapping, book_store, entered_tokens=set())
        assert len(res) >= 1
        assert any(isinstance(r, ModelValueReject) and r.reason == "book_timestamp_in_future" for r in res)

def test_engine_rejects_wide_spread(base_game, base_mapping):
    engine = ModelValueEngine()
    book_store = MagicMock()
    book_store.get.return_value = {
        "best_ask": 0.60,
        "best_bid": 0.50, # 10c spread > 5c max
        "received_at_ns": time.time_ns()
    }
    with patch("model_value_predictor.predict_probability") as mock_pred:
        mock_pred.return_value = {
            "model_probability": 0.80,
            "model_version": "test",
            "features_available": True,
            "reason": "ok"
        }
        res = engine.evaluate(base_game, base_mapping, book_store, entered_tokens=set())
        assert len(res) >= 1
        assert any(isinstance(r, ModelValueReject) and r.reason == "spread_too_large" for r in res)

def test_engine_rejects_ask_out_of_bounds(base_game, base_mapping):
    engine = ModelValueEngine()
    book_store = MagicMock()
    # ask too low
    book_store.get.return_value = {
        "best_ask": 0.04,
        "best_bid": 0.03,
        "received_at_ns": time.time_ns()
    }
    with patch("model_value_predictor.predict_probability") as mock_pred:
        mock_pred.return_value = {
            "model_probability": 0.25,
            "model_version": "test",
            "features_available": True,
            "reason": "ok"
        }
        res = engine.evaluate(base_game, base_mapping, book_store, entered_tokens=set())
        assert len(res) >= 1
        assert any(isinstance(r, ModelValueReject) and r.reason == "ask_out_of_bounds" for r in res)

def test_engine_rejects_small_edge(base_game, base_mapping):
    # If edge < 0.02
    engine = ModelValueEngine()
    book_store = MagicMock()
    # Say model probability is 0.51, ask is 0.50. Edge is 0.01 < 0.02
    with patch("model_value_predictor.predict_probability") as mock_pred:
        mock_pred.return_value = {
            "model_probability": 0.51,
            "model_version": "test",
            "features_available": True,
            "reason": "ok"
        }
        book_store.get.return_value = {
            "best_ask": 0.50,
            "best_bid": 0.49,
            "received_at_ns": time.time_ns()
        }
        res = engine.evaluate(base_game, base_mapping, book_store, entered_tokens=set())
        assert len(res) >= 1
        assert any(isinstance(r, ModelValueReject) and r.reason == "edge_too_small" for r in res)

def test_engine_chooses_highest_edge_side(base_game, base_mapping):
    engine = ModelValueEngine()
    book_store = MagicMock()
    # Mock book store returns 0.50 for radiant (best_ask), and 0.50 for dire
    book_store.get.side_effect = lambda tok: {
        "best_ask": 0.50,
        "best_bid": 0.49,
        "received_at_ns": time.time_ns()
    }
    with patch("model_value_predictor.predict_probability") as mock_pred:
        # First call (radiant): prob=0.85, ask=0.50 -> edge=0.35
        # Second call (dire): prob=0.15, ask=0.50 -> edge=-0.35
        mock_pred.side_effect = [
            {"model_probability": 0.85, "model_version": "test", "features_available": True, "reason": "ok"},
            {"model_probability": 0.15, "model_version": "test", "features_available": True, "reason": "ok"}
        ]
        res = engine.evaluate(base_game, base_mapping, book_store, entered_tokens=set())
        assert len(res) == 1
        sig = res[0]
        assert isinstance(sig, ModelValueSignal)
        assert sig.direction == "radiant"
        assert sig.edge == pytest.approx(0.35)

def test_engine_does_not_require_net_worth_leader(base_game, base_mapping):
    # Radiant is down in net worth, but model still predicts high win prob
    game = dict(base_game)
    game["radiant_net_worth"] = 8000
    game["dire_net_worth"] = 12000
    
    engine = ModelValueEngine()
    book_store = MagicMock()
    book_store.get.side_effect = lambda tok: {
        "best_ask": 0.50,
        "best_bid": 0.49,
        "received_at_ns": time.time_ns()
    }
    with patch("model_value_predictor.predict_probability") as mock_pred:
        # Say model still gives radiant 0.70 prob -> edge = 0.20
        mock_pred.side_effect = [
            {"model_probability": 0.70, "model_version": "test", "features_available": True, "reason": "ok"},
            {"model_probability": 0.30, "model_version": "test", "features_available": True, "reason": "ok"}
        ]
        res = engine.evaluate(game, base_mapping, book_store, entered_tokens=set())
        assert len(res) == 1
        assert res[0].direction == "radiant"
        assert res[0].edge == pytest.approx(0.20)
