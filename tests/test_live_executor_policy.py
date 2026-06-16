import pytest
import time
from unittest.mock import patch, MagicMock, AsyncMock

from live_executor import LiveExecutor
from execution_policy import PolicyResult

class MockBookStore:
    def get(self, token_id):
        return {"best_ask": 0.50, "best_bid": 0.40, "ask_size": 100, "received_at_ns": time.time_ns()}

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
    ex.open_positions = 0
    ex.daily_realized_pnl_usd = 0.0
    ex.disk_guard = MockDiskGuard()
    ex._get_cached_usdc_balance = AsyncMock(return_value=100.0)
    return ex

def get_base_args():
    signal = {
        "strategy_kind": "VALUE",
        "side": "YES",
        "token_id": "TOK1",
        "size_usd": 10.0,
    }
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
    return signal, mapping, game, MockBookStore()

@pytest.mark.anyio
async def test_live_executor_preserves_policy_rejection_reason_for_stale_book(executor):
    signal, mapping, game, book_store = get_base_args()
    
    # Mock evaluate_policy to reject with book_stale
    mock_result = PolicyResult(
        allowed=False,
        reason="book_stale:age_ms=1000000_max=1000",
        would_pass_live=False,
        live_skip_reason="book_stale:age_ms=1000000_max=1000",
        paper_only_bypass=False,
        price_cap=None,
        size_usd=None,
        risk_tags=("book_stale",),
    )
    
    with patch("live_executor.evaluate_policy", return_value=mock_result), \
         patch("live_executor.validate_mapping_identity", return_value=MagicMock(ok=True)):
        attempt = await executor.try_buy(
            signal=signal, mapping=mapping, game=game, book_store=book_store
        )
        
    assert attempt.order_status == "rejected_precheck"
    assert attempt.policy_allowed is False
    assert attempt.policy_reason.startswith("book_stale:")
    assert attempt.live_skip_reason.startswith("book_stale:")
    assert attempt.reason_if_rejected.startswith("book_stale:")

@pytest.mark.anyio
async def test_live_executor_preserves_policy_rejection_reason_for_match_conflict(executor):
    signal, mapping, game, book_store = get_base_args()
    
    # Mock evaluate_policy to reject with match_already_submitted
    mock_result = PolicyResult(
        allowed=False,
        reason="match_already_submitted",
        would_pass_live=False,
        live_skip_reason="match_already_submitted",
        paper_only_bypass=False,
        price_cap=None,
        size_usd=None,
        risk_tags=("match_direction_conflict",),
    )
    
    with patch("live_executor.evaluate_policy", return_value=mock_result), \
         patch("live_executor.validate_mapping_identity", return_value=MagicMock(ok=True)):
        attempt = await executor.try_buy(
            signal=signal, mapping=mapping, game=game, book_store=book_store
        )
        
    assert attempt.order_status == "rejected_precheck"
    assert attempt.policy_allowed is False
    assert attempt.policy_reason == "match_already_submitted"
    assert attempt.live_skip_reason == "match_already_submitted"
    assert attempt.reason_if_rejected == "match_already_submitted"
