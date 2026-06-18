import pytest
import pandas as pd
import json
from pathlib import Path
from generate_analysis_ready_dataset import OutcomeAggregator

def test_outcome_aggregator(tmp_path):
    # Setup mock directories
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    event_dir = tmp_path / "data_v2" / "dota_events"
    event_dir.mkdir(parents=True)
    
    # 1. Mock strategy_outcomes.csv (one won, one unknown)
    outcome_file = log_dir / "strategy_outcomes.csv"
    outcome_file.write_text("match_id,token_id,settlement_status\nm1,t1,won\nm4,t4,unknown\n")
    
    # 2. Mock shadow_outcomes_cache.json
    shadow_file = log_dir / "shadow_outcomes_cache.json"
    with open(shadow_file, "w") as f:
        json.dump({
            "m2": {"outcome": "radiant_win"},
            "m5:t5": True
        }, f)

    # 3. Mock settlement_shadow.csv
    shadow_ledger = log_dir / "settlement_shadow.csv"
    shadow_ledger.write_text("match_id,token_id,status\nm6,t6,WIN\nm7,t7,LOSS\nm8,t8,PENDING\n")
        
    # 4. Mock GAME_ENDED parquet
    parquet_file = event_dir / "events.parquet"
    df = pd.DataFrame([
        {"event_type": "GAME_ENDED", "match_id": "m3"},
        {"event_type": "TOWER_KILL", "match_id": "m3"}
    ])
    df.to_parquet(parquet_file)
    
    agg = OutcomeAggregator(root_dir=tmp_path)
    outcomes = agg.get_confirmed_outcomes()
    
    # m1:t1 should be present (won)
    assert "m1" in outcomes
    assert "t1" in outcomes["m1"]
    
    # m4:t4 should NOT be present (unknown)
    assert "m4" not in outcomes
    
    # m2 should be present (shadow cache key)
    assert "m2" in outcomes
    
    # m5:t5 should be present (composite key shadow cache)
    assert "m5" in outcomes
    assert "t5" in outcomes["m5"]
    
    # m3 should be present (GAME_ENDED)
    assert "m3" in outcomes
    
    # m6, m7 should be present (settlement shadow WIN/LOSS)
    assert "m6" in outcomes
    assert "t6" in outcomes["m6"]
    assert "m7" in outcomes
    assert "t7" in outcomes["m7"]
    
    # m8 should NOT be present (PENDING)
    assert "m8" not in outcomes

def test_data_counter(tmp_path):
    # Mock snapshots
    snap_dir = tmp_path / "data_v2" / "snapshots" / "date=2026-06-18"
    snap_dir.mkdir(parents=True)
    df_snap = pd.DataFrame({"match_id": ["m1", "m1", "m2"]})
    df_snap.to_parquet(snap_dir / "test_snaps.parquet")
    
    # Mock book ticks
    book_dir = tmp_path / "data_v2" / "book_ticks" / "date=2026-06-18"
    book_dir.mkdir(parents=True)
    df_book = pd.DataFrame({"asset_id": ["a1", "a1", "a1", "a2"]})
    df_book.to_parquet(book_dir / "test_ticks.parquet")
    
    from generate_analysis_ready_dataset import DataCounter
    counter = DataCounter(root_dir=tmp_path)
    
    snap_counts = counter.get_snapshot_counts()
    assert snap_counts["m1"] == 2
    assert snap_counts["m2"] == 1
    
    tick_counts = counter.get_book_tick_counts()
    assert tick_counts["a1"] == 3
    assert tick_counts["a2"] == 1

def test_market_filtering():
    from generate_analysis_ready_dataset import filter_markets
    markets = [
        {"name": "Team A vs Team B", "dota_match_id": "m1", "yes_token_id": "y1", "no_token_id": "n1", "market_id": "poly1", "market_type": "winner"},
        {"name": "Gorgc Streamer Battle", "dota_match_id": "m2", "yes_token_id": "y2", "no_token_id": "n2", "market_id": "poly2", "market_type": "winner"}
    ]
    snap_counts = {"m1": 5, "m2": 5}
    book_counts = {"y1": 20, "n1": 20, "y2": 20, "n2": 20}
    confirmed_matches = {"m1", "m2"}
    confirmed_tokens = set()
    
    included, report = filter_markets(markets, snap_counts, book_counts, confirmed_matches, confirmed_tokens)
    assert len(included) == 1
    assert included[0]["dota_match_id"] == "m1"
    # report[0] is m1 (included)
    # report[1] is m2 (excluded because is_streamer)
    assert report[1]["exclusion_reason"] == "is_streamer"
