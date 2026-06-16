import pytest
import sqlite3
import json
import time
from storage_v2 import StorageV2
from live_position_store import LivePositionStore, LivePosition

@pytest.fixture
def tmp_storage(tmp_path):
    db_path = str(tmp_path / "test_state_v2.sqlite")
    return StorageV2(path=db_path)

def test_storage_v2_save_and_load_positions(tmp_storage):
    pos = {
        "position_id": "P1",
        "token_id": "T1",
        "match_id": "M1",
        "entry_price": 0.50,
        "shares": 100.0,
        "cost_usd": 50.0,
        "side": "YES",
        "state": "OPEN"
    }
    tmp_storage.save_position(pos, mode="live")
    
    loaded = tmp_storage.load_positions(mode="live")
    assert len(loaded) == 1
    assert loaded[0]["position_id"] == "P1"
    assert loaded[0]["entry_price"] == 0.50

    # Upsert test
    pos["shares"] = 200.0
    pos["state"] = "PARTIALLY_EXITED"
    tmp_storage.save_position(pos, mode="live")
    
    loaded = tmp_storage.load_positions(mode="live")
    assert len(loaded) == 1
    assert loaded[0]["shares"] == 200.0
    assert loaded[0]["state"] == "PARTIALLY_EXITED"

    # remove test
    tmp_storage.remove_position("P1")
    assert len(tmp_storage.load_positions(mode="live")) == 0


def test_storage_v2_save_and_load_closed_positions(tmp_storage):
    pos = {
        "position_id": "P2",
        "token_id": "T2",
        "match_id": "M2",
        "entry_price": 0.40,
        "exit_price": 0.60,
        "shares": 100.0,
        "cost_usd": 40.0,
        "pnl_usd": 20.0,
        "side": "YES",
        "exit_reason": "take_profit"
    }
    tmp_storage.save_closed_position(pos, mode="live")
    
    loaded = tmp_storage.load_closed_positions(mode="live")
    assert len(loaded) == 1
    assert loaded[0]["position_id"] == "P2"
    assert loaded[0]["pnl_usd"] == 20.0
    assert loaded[0]["exit_reason"] == "take_profit"

    # Upsert test
    pos["pnl_usd"] = 25.0
    tmp_storage.save_closed_position(pos, mode="live")
    
    loaded = tmp_storage.load_closed_positions(mode="live")
    assert len(loaded) == 1
    assert loaded[0]["pnl_usd"] == 25.0


def test_storage_v2_save_and_load_daily_budget(tmp_storage):
    budget = {
        "total_submitted_usd": 150.0,
        "total_filled_usd": 100.0,
        "open_positions": 2,
        "daily_realized_pnl_usd": 10.5,
        "submitted_match_sides": {"M1": "YES"},
        "submitted_match_usd": {"M1": 50.0},
        "submitted_family_usd": {"value": 50.0}
    }
    tmp_storage.save_daily_budget("2026-06-16", budget)
    
    loaded = tmp_storage.load_daily_budget("2026-06-16")
    assert loaded is not None
    assert loaded["total_submitted_usd"] == 150.0
    assert loaded["open_positions"] == 2
    assert loaded["submitted_match_sides"] == {"M1": "YES"}

    assert tmp_storage.load_daily_budget("2026-06-17") is None


def test_live_position_store_uses_storage_v2(tmp_path):
    db_path = str(tmp_path / "test_positions.sqlite")
    
    # Passing a .json path should auto-convert to .sqlite in __init__
    store = LivePositionStore(path=str(tmp_path / "positions.json"))
    
    pos = LivePosition(
        position_id="LP1",
        state="OPEN",
        token_id="T1",
        opposing_token_id="T2",
        match_id="M1",
        market_name="Market",
        side="YES",
        entry_price=0.5,
        shares=100.0,
        cost_usd=50.0,
        entry_time_ns=time.time_ns(),
        entry_game_time_sec=100,
        event_type="VALUE",
        expected_move=0.0,
        fair_price=0.6,
    )
    store.add(pos)
    
    assert len(store.open_positions()) == 1
    
    # Re-load from disk
    store2 = LivePositionStore(path=str(tmp_path / "positions.json"))
    assert len(store2.open_positions()) == 1
    assert store2.positions["LP1"].cost_usd == 50.0
    
    # Mark closed
    store2.mark_closed("LP1")
    assert len(store2.open_positions()) == 0
    assert store2.positions["LP1"].state == "CLOSED"
    
    # Verify it was saved to closed_positions
    loaded_closed = store2.storage.load_closed_positions("live")
    assert len(loaded_closed) == 1
    assert loaded_closed[0]["position_id"] == "LP1"
