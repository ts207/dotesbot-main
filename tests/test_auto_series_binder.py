import pytest
from auto_series_binder import bind_event_to_steam

def test_bind_event_to_steam_reversed_side():
    markets_yaml = {"markets": []}
    market = {
        "id": "123",
        "question": "Winner",
        "conditionId": "0x1",
        "clobTokenIds": '["T1", "T2"]',
        "outcomes": '["Team A", "Team B"]'
    }
    event = {
        "startDate": "2026-06-16T12:00:00Z"
    }
    steam_match = {
        "match_id": 999,
        "radiant_team": "Team B",
        "dire_team": "Team A"
    }
    
    res = bind_event_to_steam(market, event, steam_match, markets_yaml, force_map_winner=True)
    assert res is True
    assert len(markets_yaml["markets"]) == 1
    m = markets_yaml["markets"][0]
    
    assert m["steam_side_mapping"] == "reversed"
    assert m["dota_match_id"] == "999"
    assert m["market_type"] == "MAP_WINNER"
