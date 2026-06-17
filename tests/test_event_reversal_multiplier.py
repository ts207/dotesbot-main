import pytest
import time
from event_triggered_value_engine import EventTriggeredValueEngine
from actual_dota_event_types import ActualDotaEvent
from config import EVENT_VALUE_TRADE_USD

def test_event_reversal_multiplier(monkeypatch):
    # Ensure the feature is enabled for the test
    monkeypatch.setattr("event_triggered_value_engine.EVENT_TRIGGERED_VALUE_ENABLED", True)
    
    engine = EventTriggeredValueEngine()
    
    game = {
        "match_id": "test_match",
        "received_at_ns": time.time_ns(),
        "data_source": "top_live",
        "game_time_sec": 1000,
        "radiant_lead": -2000,
        "radiant_score": 10,
        "dire_score": 15,
        "net_worth_radiant": 30000,
        "net_worth_dire": 32000,
    }
    
    event = ActualDotaEvent(
        event_id="test_event",
        event_type="MULTI_KILL_WINDOW",
        side="radiant",
        radiant_lead_before=-5000,
        radiant_lead_after=-2000,
        game_time_sec=1000,
        received_at_ns=time.time_ns(),
        source="top_live",
        match_id="test_match",
        lobby_id=123,
        league_id=456
    )
    
    mapping = {
        "market_type": "MAP_WINNER",
        "steam_side_mapping": "normal",
        "yes_token_id": "token_yes",
        "no_token_id": "token_no"
    }
    
    book_store = {
        "token_yes": {
            "best_ask": "0.35",
            "received_at_ns": time.time_ns(),
            "market_price_before_event": 0.20
        }
    }
    
    class FakeFairResult:
        def __init__(self, fair):
            self.model_available = True
            self.model_reason = "ok"
            self.fair_raw = fair
            self.fair_used = fair
            self.fair = fair
            self.elo_diff = 0.0

    def fake_compute_side_fair(game, side, radiant_lead_override, **kwargs):
        if radiant_lead_override == -5000:
            return FakeFairResult(0.40)
        elif radiant_lead_override == -2000:
            return FakeFairResult(0.60)
        return FakeFairResult(0.50)

    monkeypatch.setattr("event_triggered_value_engine.compute_side_fair", fake_compute_side_fair)
    
    def fake_evaluate_policy(*args, **kwargs):
        from execution_policy import PolicyResult
        return PolicyResult(allowed=True, reason="ok", expected_value=0.0)
    monkeypatch.setattr("event_triggered_value_engine.evaluate_policy", fake_evaluate_policy)

    class FakeStateCheck:
        ok = True
        missing_fields = []
        reason = ""
    def fake_validate_top_live_state(game):
        return FakeStateCheck()
    monkeypatch.setattr("event_triggered_value_engine.validate_top_live_state", fake_validate_top_live_state)
    
    results = engine.evaluate(
        event=event,
        game=game,
        mapping=mapping,
        book_store=book_store,
    )
    
    assert len(results) == 1
    signal = results[0]
    assert not hasattr(signal, "reason"), getattr(signal, "reason", "")
    assert signal.is_reversal == True
    
    expected_size = EVENT_VALUE_TRADE_USD * 2.0
    assert signal.sized_usd == expected_size, f"Expected {expected_size}, got {signal.sized_usd}"
