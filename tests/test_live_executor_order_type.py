import pytest
import time
from unittest.mock import patch, MagicMock, AsyncMock

from live_executor import LiveExecutor

class MockBookStore:
    def __init__(self):
        self.book = {"best_ask": 0.50, "best_bid": 0.40, "ask_size": 100, "received_at_ns": time.time_ns()}
    def get(self, token_id):
        return self.book
    def get_book(self, token_id):
        return self.book

class MockDiskGuard:
    def reject_reason(self):
        return None

@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.fixture
def executor():
    ex = LiveExecutor.__new__(LiveExecutor)
    ex.mode = "live"
    ex._submitted_match_usd = {}
    ex._submitted_family_usd = {}
    ex._submitted_match_sides = {}
    ex.total_submitted_usd = 0.0
    ex.total_filled_usd = 0.0
    ex.open_positions = 0
    ex.daily_realized_pnl_usd = 0.0
    ex.disk_guard = MockDiskGuard()
    ex._value_last_entry_ns = {}
    ex._get_cached_usdc_balance = AsyncMock(return_value=100.0)
    ex.remaining_budget = MagicMock(return_value=100.0)
    return ex

def _make_signal() -> MagicMock:
    signal = MagicMock()
    signal.token_id = "TOK1"
    signal.side = "YES"
    signal.direction = "team1"
    signal.sized_usd = 10.0
    signal.fair_price = 0.80
    signal.edge = 0.15
    signal.book_age_ms = 100
    signal.game_time_sec = 1000
    signal.strategy_kind = "VALUE_EDGE"
    return signal

@pytest.mark.anyio
async def test_try_buy_value_uses_fok_when_order_type_is_fok(executor):
    mapping = {"market_type": "MAP_WINNER", "tick_size": 0.01, "yes_token_id": "TOK1"}
    game = {"match_id": "M1", "data_source": "top_live", "received_at_ns": time.time_ns()}
    book_store = MockBookStore()
    signal = _make_signal()
    
    meta_return = ("VALUE_EDGE", "VALUE", "some_subtype", False, False)

    with patch("live_executor.LIVE_ORDER_TYPE", "FOK"), \
         patch("live_executor.ENABLE_REAL_LIVE_TRADING", True), \
         patch("live_executor.validate_mapping_identity", return_value=MagicMock(ok=True)), \
         patch("live_executor._value_signal_strategy_meta", return_value=meta_return), \
         patch.object(executor, "_save"):
         
        executor.client = MagicMock()
        executor.client.buy_fok_market = AsyncMock(return_value={"order_id": "123", "status": "matched", "filled_size": 10.0, "avg_price": 0.50})
        executor.client.buy_fak_market = AsyncMock(side_effect=AssertionError("Should not call FAK"))

        attempt = await executor.try_buy_value(
            signal=signal, mapping=mapping, game=game, book_store=book_store
        )
        
    executor.client.buy_fok_market.assert_called_once()
    assert attempt.order_status in ("matched", "filled")

@pytest.mark.anyio
async def test_try_buy_uses_fok_when_order_type_is_fok(executor):
    mapping = {"market_type": "MAP_WINNER", "tick_size": 0.01, "yes_token_id": "TOK1"}
    game = {"match_id": "M1", "data_source": "top_live", "received_at_ns": time.time_ns()}
    book_store = MockBookStore()
    signal_dict = {
        "event_type": "POLL_FIGHT_SWING",
        "strategy_kind": "EVENT",
        "side": "YES",
        "size_usd": 10.0,
        "token_id": "TOK1",
    }
    
    with patch("live_executor.LIVE_ORDER_TYPE", "FOK"), \
         patch("live_executor.ENABLE_REAL_LIVE_TRADING", True), \
         patch("live_executor.validate_mapping_identity", return_value=MagicMock(ok=True)), \
         patch("live_executor.evaluate_policy", return_value=MagicMock(allowed=True, price_cap=0.60)), \
         patch.object(executor, "_save"):
         
        executor.client = MagicMock()
        executor.client.buy_fok_market = AsyncMock(return_value={"order_id": "123", "status": "matched", "filled_size": 10.0, "avg_price": 0.50})
        executor.client.buy_fak_market = AsyncMock(side_effect=AssertionError("Should not call FAK"))

        attempt = await executor.try_buy(
            signal=signal_dict, mapping=mapping, game=game, book_store=book_store
        )
        
    executor.client.buy_fok_market.assert_called_once()
    assert attempt.order_status in ("matched", "filled")

@pytest.mark.anyio
async def test_invalid_order_type_rejected_before_submission(executor):
    mapping = {"market_type": "MAP_WINNER", "tick_size": 0.01, "yes_token_id": "TOK1"}
    game = {"match_id": "M1", "data_source": "top_live", "received_at_ns": time.time_ns()}
    book_store = MockBookStore()
    signal_dict = {
        "event_type": "POLL_FIGHT_SWING",
        "strategy_kind": "EVENT",
        "side": "YES",
        "size_usd": 10.0,
        "token_id": "TOK1",
    }
    
    with patch("live_executor.LIVE_ORDER_TYPE", "INVALID_TYPE"), \
         patch("live_executor.validate_mapping_identity", return_value=MagicMock(ok=True)), \
         patch("live_executor.evaluate_policy", return_value=MagicMock(allowed=True, price_cap=0.60)), \
         patch.object(executor, "_save"):
         
        executor.client = MagicMock()
        executor.client.buy_fak_market = AsyncMock()
        executor.client.buy_fok_market = AsyncMock()
        executor.client.buy_gtc_limit = AsyncMock()

        attempt = await executor.try_buy(
            signal=signal_dict, mapping=mapping, game=game, book_store=book_store
        )
        
    assert attempt.order_status == "rejected_precheck"
    assert attempt.reason_if_rejected == "order_type_not_allowed"
    executor.client.buy_fak_market.assert_not_called()
    executor.client.buy_fok_market.assert_not_called()
