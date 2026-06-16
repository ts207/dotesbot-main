import pytest
import time
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime

import main
from main import steam_loop
from poly_ws import BookStore
from paper_trader import PaperTrader
from storage import (
    SignalLogger, DotaEventLogger, PositionLogger, RawSnapshotLogger,
    LatencyLogger, RichContextLogger, SourceDelayLogger,
    BookRefreshRescueLogger, MatchWinnerSignalLogger, SignalMarkoutLogger
)
from signal_engine import EventSignalEngine
from event_detector import EventDetector, DotaEvent
from liveleague_features import LiveLeagueContextCache

@pytest.mark.asyncio
async def test_match_winner_sidecar_integration(tmp_path):
    # Mock loggers
    signal_logger = MagicMock(spec=SignalLogger)
    event_logger = MagicMock(spec=DotaEventLogger)
    position_logger = MagicMock(spec=PositionLogger)
    snapshot_logger = MagicMock(spec=RawSnapshotLogger)
    latency_logger = MagicMock(spec=LatencyLogger)
    rich_context_logger = MagicMock(spec=RichContextLogger)
    source_delay_logger = MagicMock(spec=SourceDelayLogger)
    rescue_logger = MagicMock(spec=BookRefreshRescueLogger)
    
    match_winner_logger = MatchWinnerSignalLogger(log_dir=str(tmp_path))
    match_winner_logger.log_match_signal = MagicMock()
    
    # Mock store and engine
    book_store = BookStore()
    book_store.update_direct("map_yes", best_bid=0.40, best_ask=0.45, bid_size=100, ask_size=100)
    book_store.update_direct("map_no", best_bid=0.50, best_ask=0.55, bid_size=100, ask_size=100)
    book_store.update_direct("match_yes", best_bid=0.60, best_ask=0.65, bid_size=100, ask_size=100)
    book_store.update_direct("match_no", best_bid=0.30, best_ask=0.35, bid_size=100, ask_size=100)
    trader = PaperTrader()
    signal_engine = EventSignalEngine()
    trader = PaperTrader()
    signal_engine = EventSignalEngine()
    
    # Mock mappings
    mappings = [
        {
            "dota_match_id": "123",
            "market_type": "MATCH_WINNER",
            "series_type": 1, # BO3
            "current_game_number": 2,
            "series_score_yes": 0,
            "series_score_no": 1,
            "yes_token_id": "match_yes",
            "no_token_id": "match_no",
            "yes_team": "Team A",
            "no_team": "Team B",
            "confidence": 1.0,
        },
        {
            "dota_match_id": "123",
            "market_type": "MAP_WINNER",
            "game_number": 2,
            "yes_token_id": "map_yes",
            "no_token_id": "map_no",
            "yes_team": "Team A",
            "no_team": "Team B",
            "confidence": 1.0,
        }
    ]
    
    asset_ids = ["map_yes", "map_no", "match_yes", "match_no"]

    # Mock fetch_all_live_games to return a game
    async def mock_fetch(session, cache=None, **kwargs):
        return [
            {
                "match_id": "123",
                "game_time_sec": 600,
                "radiant_team": "Team A",
                "dire_team": "Team B",
                "data_source": "top_live",
                "score": [10, 5],
                "radiant_net_worth": 15000,
                "dire_net_worth": 10000,
                "received_at_ns": time.time_ns(),
            }
        ]

    async def mock_discover(*args, **kwargs):
        return None
        
    # Mock event detector to force an event
    event_detector = MagicMock(spec=EventDetector)
    def mock_observe(game, mapping):
        # Only yield for the MAP_WINNER, wait - actually MATCH_WINNER mapping also receives observe?
        # main.py does event_detector.observe(game, mapping) for ALL mappings.
            return [DotaEvent(
                match_id="123",
                lobby_id=None,
                league_id=None,
                event_type="POLL_VALUE_DISAGREEMENT",
                game_time_sec=600,
                radiant_team="Team A",
                dire_team="Team B",
                radiant_lead=5000,
                radiant_score=10,
                dire_score=5,
                tower_state=0,
                previous_value=None,
                current_value=None,
                delta=3000,
                window_sec=30,
                direction="radiant" if mapping["yes_team"] == "Team A" else "dire",
                severity="medium"
            )]
    event_detector.observe.side_effect = mock_observe
    
    signal_markout_logger = MagicMock(spec=SignalMarkoutLogger)
    
    with patch('main.ENABLE_MATCH_WINNER_RESEARCH', True), \
         patch('main.ENABLE_MATCH_WINNER_TRADING', True), \
         patch('main.EVENT_DETECTORS_ENABLED', True), \
         patch('signal_engine.ENABLE_MATCH_WINNER_TRADING', True), \
         patch('signal_engine.S3_ENABLED', False), \
         patch('signal_engine.MAX_BOOK_AGE_MS', 99999999):

        # Mock asyncio.sleep to break the loop after first iteration
        async def mock_sleep(*args, **kwargs):
            raise asyncio.CancelledError()

        with patch('main.fetch_all_live_games', new=mock_fetch), \
             patch('main.sync_markets_to_games', return_value=[]), \
             patch('main.discover_markets_main', new=mock_discover), \
             patch('main.load_valid_mappings', return_value=(mappings, [])), \
             patch('asyncio.sleep', new=mock_sleep):
            try:
                await steam_loop(
                    book_store=book_store,
                    trader=trader,
                    signal_logger=signal_logger,
                    event_detector=event_detector,
                    signal_engine=signal_engine,
                    event_logger=event_logger,
                    position_logger=position_logger,
                    snapshot_logger=snapshot_logger,
                    latency_logger=latency_logger,
                    live_executor=None,
                    live_logger=None,
                    rich_context_logger=rich_context_logger,
                    source_delay_logger=source_delay_logger,
                    rescue_logger=rescue_logger,
                    match_winner_logger=match_winner_logger,
                    signal_markout_logger=signal_markout_logger,
                    mappings=mappings,
                    asset_ids=asset_ids
                )
            except asyncio.CancelledError:
                pass
            
    # Check that match_winner_logger.append was called
    assert match_winner_logger.log_match_signal.call_count >= 1
    
    # Check arguments
    found = False
    for call in match_winner_logger.log_match_signal.call_args_list:
        args = call[0][0]
        if args.get("decision") == "skip" and args.get("skip_reason") == "research_mode_match_winner":
            found = True
            assert args["match_id"] == "123"
            assert args["event_type"] == "POLL_VALUE_DISAGREEMENT"
            assert "match_fair_after" in args
            assert args["map_bid"] == 0.40 or args["map_bid"] == 0.50
    assert found
