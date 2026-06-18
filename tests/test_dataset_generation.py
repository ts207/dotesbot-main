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
    
    # 1. Mock strategy_outcomes.csv
    outcome_file = log_dir / "strategy_outcomes.csv"
    outcome_file.write_text("match_id,token_id,settlement_status\nm1,t1,won\n")
    
    # 2. Mock shadow_outcomes_cache.json
    shadow_file = log_dir / "shadow_outcomes_cache.json"
    with open(shadow_file, "w") as f:
        json.dump({"m2": {"outcome": "radiant_win"}}, f)
        
    # 3. Mock GAME_ENDED parquet
    parquet_file = event_dir / "events.parquet"
    df = pd.DataFrame([
        {"event_type": "GAME_ENDED", "match_id": "m3"},
        {"event_type": "TOWER_KILL", "match_id": "m3"}
    ])
    df.to_parquet(parquet_file)
    
    agg = OutcomeAggregator(root_dir=tmp_path)
    confirmed_matches, confirmed_tokens = agg.get_confirmed_outcomes()
    
    assert "m1" in confirmed_matches
    assert "t1" in confirmed_tokens
    assert "m2" in confirmed_matches
    assert "m3" in confirmed_matches
