import json
import os
from pathlib import Path

from live_state import save_live_state, load_live_state
from live_position_store import LivePositionStore, LivePosition

def test_live_state_atomic_write(monkeypatch, tmp_path):
    monkeypatch.setattr("live_state.LIVE_STATE_PATH", str(tmp_path / "live_state.json"))
    
    save_live_state(open_positions=1, total_submitted_usd=1.0, total_filled_usd=0.0)
    
    assert (tmp_path / "live_state.json").exists()
    assert not (tmp_path / "live_state.json.tmp").exists()
    
    data = load_live_state()
    assert data["open_positions"] == 1
    assert data["total_submitted_usd"] == 1.0

def test_position_store_atomic_write(tmp_path):
    store = LivePositionStore(str(tmp_path / "live_positions.json"))
    
    pos = LivePosition(
        position_id="P1", match_id="M1", token_id="T1", opposing_token_id="T2", 
        market_name="test", side="yes", state="OPEN",
        shares=10.0, cost_usd=5.0, entry_price=0.5, entry_time_ns=0, entry_game_time_sec=0,
        event_type="test", expected_move=0.0, fair_price=0.5,
        strategy_kind="test", entry_engine="test", exit_engine="test", backed_direction="radiant"
    )
    store.positions["P1"] = pos
    
    store.save()
    
    assert (tmp_path / "live_positions.json").exists()
    assert not (tmp_path / "live_positions.json.tmp").exists()
    
    loaded_store = LivePositionStore(str(tmp_path / "live_positions.json"))
    assert len(loaded_store.positions) == 1
    assert loaded_store.positions["P1"].position_id == "P1"

def test_0_byte_read_recovery(tmp_path):
    # If a file is 0-bytes, it should load gracefully or return empty/default
    # PositionStore has logic to return [] on JSONDecodeError.
    f_path = tmp_path / "live_positions.json"
    f_path.write_text("")  # 0 byte
    
    store = LivePositionStore(str(f_path))
    assert store.positions == {}

