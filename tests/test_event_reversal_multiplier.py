import pytest
from actual_dota_event_types import ActualDotaEvent
from event_triggered_value_engine import EventTriggeredValueEngine
from dataclasses import dataclass

@dataclass
class DummyConfig:
    value_min_edge: float = 0.05
    value_max_price: float = 0.84
    value_min_game_time: int = 600
    paper_trade_size_usd: float = 25.0
    value_max_per_match: float = 100.0

def test_event_reversal_multiplier():
    from unittest.mock import Mock
    
    cfg = DummyConfig()
    # Assume EVENT_VALUE_TRADE_USD is derived from paper_trade_size_usd or configured directly
    # We will just verify the logic locally if possible, or monkeypatch
    pass
