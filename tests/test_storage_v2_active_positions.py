import json
import pytest
from storage_v2 import StorageV2, ACTIVE_POSITION_STATES

def _save_pos(storage: StorageV2, pos_id: str, state: str, mode: str):
    pos = {"position_id": pos_id, "state": state, "token_id": "tok1", "match_id": "m1"}
    storage.save_position(pos, mode=mode)

def test_load_positions_active_only_excludes_closed(tmp_path):
    db_path = str(tmp_path / "state.sqlite")
    storage = StorageV2(db_path)
    
    _save_pos(storage, "p1", "OPEN", "live")
    _save_pos(storage, "p2", "CLOSED", "live")
    _save_pos(storage, "p3", "REJECTED", "live")
    _save_pos(storage, "p4", "CANCELLED", "live")
    
    positions = storage.load_positions(mode="live", active_only=True)
    assert len(positions) == 1
    assert positions[0]["position_id"] == "p1"

def test_load_positions_active_only_includes_pending_entry_and_exiting(tmp_path):
    db_path = str(tmp_path / "state.sqlite")
    storage = StorageV2(db_path)
    
    _save_pos(storage, "p1", "PENDING_ENTRY", "live")
    _save_pos(storage, "p2", "PARTIALLY_EXITED", "live")
    _save_pos(storage, "p3", "PENDING_EXIT_GTC", "live")
    _save_pos(storage, "p4", "EXITING", "live")
    
    positions = storage.load_positions(mode="live", active_only=True)
    assert len(positions) == 4
    ids = {p["position_id"] for p in positions}
    assert ids == {"p1", "p2", "p3", "p4"}

def test_load_positions_inactive_false_returns_all_rows(tmp_path):
    db_path = str(tmp_path / "state.sqlite")
    storage = StorageV2(db_path)
    
    _save_pos(storage, "p1", "OPEN", "live")
    _save_pos(storage, "p2", "CLOSED", "live")
    
    positions = storage.load_positions(mode="live", active_only=False)
    assert len(positions) == 2
    
def test_live_position_store_load_excludes_closed_rows_left_in_positions_table(tmp_path, monkeypatch):
    from live_position_store import LivePositionStore
    from unittest.mock import patch
    
    with patch("storage_v2.StorageV2.load_positions") as mock_load:
        mock_load.return_value = []
        store = LivePositionStore()
        mock_load.assert_called_with(mode="live", active_only=True)

def test_paper_trader_load_open_positions_excludes_closed_rows_left_in_positions_table(tmp_path, monkeypatch):
    from paper_trader import PaperTrader
    from unittest.mock import patch
    
    with patch("storage_v2.StorageV2.load_positions") as mock_load:
        mock_load.return_value = []
        trader = PaperTrader()
        trader.load_open_positions()
        mock_load.assert_called_once_with(mode="paper", active_only=True)
