import sqlite3
import pytest
import os
from storage_v2 import StorageV2

def test_live_state_atomic_write(tmp_path):
    """
    Verify that StorageV2 daily_budget saves are atomic.
    We test this by ensuring that a failed transaction rolls back.
    """
    db_path = str(tmp_path / "state.sqlite")
    storage = StorageV2(db_path)
    
    # Save valid initial state
    storage.save_daily_budget("2026-06-17", {"total_submitted_usd": 1.0}, mode="real_live")
    
    # Attempt an invalid save that will crash mid-transaction (e.g. schema violation or type error)
    # Since sqlite allows some dynamic typing, we can force an error by injecting bad SQL or
    # by raising an exception inside a patch. Here we mock conn.execute to fail on the UPSERT.
    class BrokenException(Exception): pass
    
    original_connect = storage.connect
    
    # We create a wrapper that will raise an error after beginning the transaction
    with pytest.raises(BrokenException):
        with storage.connect() as conn:
            conn.execute("UPDATE daily_budgets SET total_submitted_usd = 999.0 WHERE mode = 'real_live'")
            raise BrokenException("Simulated crash")
            
    # Verify the rollback happened and 1.0 is still the value, not 999.0
    with storage.connect() as conn:
        val = conn.execute("SELECT total_submitted_usd FROM daily_budgets WHERE mode = 'real_live'").fetchone()[0]
        assert val == 1.0

def test_position_store_atomic_write(tmp_path):
    """
    Verify that StorageV2 position saves are atomic.
    """
    db_path = str(tmp_path / "state.sqlite")
    storage = StorageV2(db_path)
    
    pos = {
        "position_id": "P1", "token_id": "T1", "match_id": "M1", 
        "state": "OPEN", "mode": "live"
    }
    storage.save_position(pos, mode="live")
    
    class BrokenException(Exception): pass
    
    with pytest.raises(BrokenException):
        with storage.connect() as conn:
            conn.execute("UPDATE positions SET state = 'CLOSED' WHERE position_id = 'P1'")
            raise BrokenException("Simulated crash")
            
    # Verify rollback
    with storage.connect() as conn:
        state = conn.execute("SELECT state FROM positions WHERE position_id = 'P1'").fetchone()[0]
        assert state == "OPEN"
