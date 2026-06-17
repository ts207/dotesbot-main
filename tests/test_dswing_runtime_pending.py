import pytest
import time
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

from runtime.bot_runtime import steam_loop
from live_executor import LiveExecutor, LiveOrderAttempt

@pytest.fixture
def anyio_backend():
    return "asyncio"

class MockBookStore:
    def __init__(self):
        self.book = {"best_ask": 0.50, "best_bid": 0.40, "ask_size": 100, "received_at_ns": time.time_ns()}
    def get(self, token_id):
        return self.book
    def get_book(self, token_id):
        return self.book
    def get_snapshot_before(self, token_id, ts):
        return self.book
    def update(self, token_id, book):
        self.book.update(book)

@pytest.mark.anyio
async def test_dswing_pending_entry():
    book_store = MockBookStore()
    book_store.update("TOK1", {"best_ask": 0.5})

    trader = MagicMock()
    live_executor = AsyncMock(spec=LiveExecutor)
    position_logger = MagicMock()
    signal_logger = MagicMock()
    live_position_store = MagicMock()
    live_logger = MagicMock()

    game = {
        "match_id": "M1",
        "lobby_id": "L1",
        "game_time_sec": 1000,
        "radiant_lead": 10000,
        "radiant_score": 10,
        "dire_score": 5,
        "game_number": 3,
        "series_type": 1,
        "data_source": "top_live",
        "received_at_ns": time.time_ns(),
        "game_state": 5,
    }

    class FakeCandidate:
        def __init__(self):
            self.strategy = "DSWING"
            self.fair_price = 0.9
            self.edge = 0.1
            self.token_id = "TOK1"
            self.side = "YES"
            self.sized_usd = 10.0
            self.p_game = 0.95
            self.lead = 10000
            self.series_fair = 0.9
            self.ask = 0.5
            self.book_age_ms = 100
            self.signal_id = "sig1"
            self.direction = "radiant"
            self.edge_type = "dswing"
            self.target_horizon = 0
            self.expected_hold_sec = 0
            self.entry_trigger = 0
            self.exit_trigger = 0
            self.primary_metric = "a"
            self.secondary_metric = "b"
            self.promotion_rule = "c"
            self.disable_rule = "d"
            self.match_id = "M1"
            self.signal = self
            self.game_time_sec = 1000

    cand = FakeCandidate()

    attempt = LiveOrderAttempt(
        event_type="DSWING",
        event_direction="radiant",
        token_id="TOK1",
        side="YES",
        fair_price=0.9,
        best_ask=0.5,
        price_cap=0.6,
        edge=0.1,
        lag=None,
        spread=None,
        book_age_ms=100,
        steam_age_ms=None,
        order_type="FAK",
        submitted_size_usd=10.0,
        market_name="Test Market",
        match_id="M1",
        game_time_sec=1000,
        created_at_ns=time.time_ns(),
        trader_kind="dswing",
        exit_horizon_sec=None,
        signal_id="sig1",
        strategy_kind="DSWING",
        strategy_family="DSWING",
        strategy_subtype=None,
        is_reversal=False,
        is_continuation=False,
    )

    mapping = {"dota_match_id": "M1", "yes_token_id": "TOK1", "no_token_id": "TOK2", "name": "Test Market", "market_type": "MAP_WINNER", "yes_team": "Team A", "no_team": "Team B", "confidence": 1.0}

    kwargs = {
        "book_store": book_store,
        "trader": trader,
        "signal_logger": signal_logger,
        "event_detector": MagicMock(),
        "signal_engine": MagicMock(),
        "event_logger": MagicMock(),
        "position_logger": position_logger,
        "snapshot_logger": MagicMock(),
        "latency_logger": MagicMock(),
        "live_executor": live_executor,
        "live_logger": live_logger,
        "rich_context_logger": MagicMock(),
        "source_delay_logger": MagicMock(),
        "rescue_logger": MagicMock(),
        "match_winner_logger": MagicMock(),
        "signal_markout_logger": MagicMock(),
        "mappings": [mapping],
        "asset_ids": ["TOK1"],
        "live_position_store": live_position_store,
        "http_session": AsyncMock(),
    }

    # Scenario 1: matched -> OPEN
    attempt.order_status = "matched"
    attempt.filled_size_usd = 10.0
    attempt.avg_fill_price = 0.6
    live_executor.try_buy_value.return_value = attempt

    def mock_allocate(*args, **kwargs):
        return [MagicMock(winner=cand)]

    call_count = 0
    async def mock_fetch(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return [game]
        raise asyncio.CancelledError()

    with patch("runtime.bot_runtime.allocate_candidates", side_effect=mock_allocate), \
         patch("runtime.bot_runtime.fetch_all_live_games", side_effect=mock_fetch), \
         patch("runtime.bot_runtime.load_valid_mappings", return_value=([mapping], [])), \
         patch("config.ENABLE_REAL_LIVE_TRADING", True), \
         patch("config.STEAM_API_KEY", "fake_key"), \
         patch("runtime.bot_runtime.ENABLE_REAL_LIVE_TRADING", True):

        try:
            await steam_loop(**kwargs)
        except asyncio.CancelledError:
            pass

        live_position_store.add.assert_called()
    pos = live_position_store.add.call_args[0][0]
    assert pos.state == "OPEN", "Matched order should be OPEN"
    assert pos.pending_entry_order_id is None

    # Scenario 2: delayed -> PENDING_ENTRY
    live_position_store.add.reset_mock()
    attempt.order_status = "delayed"
    attempt.order_id = "12345"
    attempt.filled_size_usd = 0.0

    call_count_2 = 0
    async def mock_fetch_2(*args, **kwargs):
        nonlocal call_count_2
        call_count_2 += 1
        if call_count_2 <= 2:
            return [game]
        raise asyncio.CancelledError()

    with patch("runtime.bot_runtime.allocate_candidates", return_value=[MagicMock(winner=cand)]), \
         patch("runtime.bot_runtime.fetch_all_live_games", side_effect=mock_fetch_2), \
         patch("runtime.bot_runtime.load_valid_mappings", return_value=([mapping], [])), \
         patch("config.ENABLE_REAL_LIVE_TRADING", True), \
         patch("config.STEAM_API_KEY", "fake_key"), \
         patch("runtime.bot_runtime.ENABLE_REAL_LIVE_TRADING", True):

        try:
            await steam_loop(**kwargs)
        except asyncio.CancelledError:
            pass

    live_position_store.add.assert_called()
    pos2 = live_position_store.add.call_args[0][0]
    assert pos2.state == "PENDING_ENTRY", "Delayed order should be PENDING_ENTRY"

    # Scenario 3: rejected -> no LivePosition
    live_position_store.add.reset_mock()
    attempt.order_status = "rejected"
    attempt.order_id = None
    attempt.filled_size_usd = 0.0

    call_count_3 = 0
    async def mock_fetch_3(*args, **kwargs):
        nonlocal call_count_3
        call_count_3 += 1
        if call_count_3 <= 2:
            return [game]
        raise asyncio.CancelledError()

    with patch("runtime.bot_runtime.allocate_candidates", return_value=[MagicMock(winner=cand)]), \
         patch("runtime.bot_runtime.fetch_all_live_games", side_effect=mock_fetch_3), \
         patch("runtime.bot_runtime.load_valid_mappings", return_value=([mapping], [])), \
         patch("config.ENABLE_REAL_LIVE_TRADING", True), \
         patch("config.STEAM_API_KEY", "fake_key"), \
         patch("runtime.bot_runtime.ENABLE_REAL_LIVE_TRADING", True):

        try:
            await steam_loop(**kwargs)
        except asyncio.CancelledError:
            pass

    live_position_store.add.assert_not_called()
