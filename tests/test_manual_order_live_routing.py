import pytest
import time
from unittest.mock import patch, MagicMock, AsyncMock

from live_executor import LiveExecutor

@pytest.fixture
def anyio_backend():
    return "asyncio"

class MockBookStore:
    def __init__(self):
        self.book = {"best_ask": 0.50, "best_bid": 0.40, "ask_size": 100, "received_at_ns": time.time_ns()}
    def get_book(self, token_id):
        return self.book

class MockDiskGuard:
    def reject_reason(self):
        return None

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

@pytest.mark.anyio
async def test_manual_order_policy_rejects_exceeding_match_limit(executor):
    mapping = {"yes_token_id": "TOK1", "no_token_id": "TOK2", "market_type": "MAP_WINNER", "tick_size": "0.01", "name": "TeamA vs TeamB"}
    signal = {"side": "YES", "size_usd": 20.0, "price_cap": 0.60}
    book_store = MockBookStore()
    
    executor._submitted_match_usd["M1"] = 40.0
    
    with patch("config.ENABLE_REAL_LIVE_TRADING", True), \
         patch("manual_order_policy.MAX_TRADE_USD", 100.0), \
         patch("manual_order_policy.MAX_TOTAL_LIVE_USD", 1000.0), \
         patch("manual_order_policy.MAX_OPEN_POSITIONS", 10), \
         patch("manual_order_policy.MAX_OPEN_USD_PER_MATCH", 50.0), \
         patch.object(executor, "_save"):
         
        attempt = await executor.try_buy_manual(
            signal=signal,
            mapping=mapping,
            token_id="TOK1",
            match_id="M1",
            book_store=book_store
        )
        
    assert attempt.order_status == "rejected_precheck"
    assert "MAX_OPEN_USD_PER_MATCH exceeded" in attempt.reason_if_rejected

@pytest.mark.anyio
async def test_manual_order_policy_allows_valid_order(executor):
    mapping = {"yes_token_id": "TOK1", "no_token_id": "TOK2", "market_type": "MAP_WINNER", "tick_size": "0.01", "name": "TeamA vs TeamB"}
    signal = {"side": "YES", "size_usd": 10.0, "price_cap": 0.60}
    book_store = MockBookStore()
    
    with patch("config.ENABLE_REAL_LIVE_TRADING", True), \
         patch("manual_order_policy.MAX_TRADE_USD", 100.0), \
         patch("manual_order_policy.MAX_TOTAL_LIVE_USD", 1000.0), \
         patch("manual_order_policy.MAX_OPEN_POSITIONS", 10), \
         patch.object(executor, "_save"):
        
        executor.client = MagicMock()
        executor.client.buy_fak_market = AsyncMock(return_value={"order_id": "123", "status": "matched", "filled_size": 10.0, "avg_price": 0.50})
        
        attempt = await executor.try_buy_manual(
            signal=signal,
            mapping=mapping,
            token_id="TOK1",
            match_id="M1",
            book_store=book_store
        )
        
    assert attempt.order_status in ("matched", "filled", "submitting")
