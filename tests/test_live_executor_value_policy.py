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
    ex.last_reset_date = "2026-06-17"
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
    # Give some budget
    ex.remaining_budget = MagicMock(return_value=100.0)
    return ex

def _make_signal(kind: str) -> MagicMock:
    signal = MagicMock()
    signal.token_id = "TOK1"
    signal.side = "YES"
    signal.direction = "team1"
    signal.sized_usd = 10.0
    signal.fair_price = 0.80
    signal.edge = 0.15
    signal.book_age_ms = 100
    signal.game_time_sec = 1000
    signal.strategy_kind = kind
    return signal

@pytest.mark.anyio
@pytest.mark.parametrize("strategy_kind", [
    "VALUE_EDGE",
    "EVENT_CONTINUATION_EDGE",
    "EVENT_REVERSAL_EDGE",
    "DSWING"
])
async def test_try_buy_value_does_not_reject_valid_strategy_as_event_not_allowed(executor, strategy_kind):
    mapping = {
        "market_type": "MAP_WINNER",
        "tick_size": 0.01,
        "yes_token_id": "TOK1",
    }
    game = {
        "match_id": "M1",
        "data_source": "top_live",
        "received_at_ns": time.time_ns(),
    }
    book_store = MockBookStore()
    
    signal = _make_signal(strategy_kind)
    
    is_rev = strategy_kind == "EVENT_REVERSAL_EDGE"
    is_cont = strategy_kind == "EVENT_CONTINUATION_EDGE"
    meta_return = (strategy_kind, "VALUE", "some_subtype", is_rev, is_cont)

    with patch("live_executor.validate_mapping_identity", return_value=MagicMock(ok=True)), \
         patch("live_executor._value_signal_strategy_meta", return_value=meta_return):
        
        executor.client = MagicMock()
        executor.client.buy_fak_market = AsyncMock(return_value={"order_id": "123", "status": "matched", "filled_size": 10.0, "avg_price": 0.50})
        
        with patch.object(executor, "_save"):
            attempt = await executor.try_buy_value(
                signal=signal, mapping=mapping, game=game, book_store=book_store
            )
        
    if attempt.order_status == "rejected_precheck":
        assert "event_not_allowed" not in attempt.reason_if_rejected, f"Failed for {strategy_kind} - rejected as event_not_allowed"
