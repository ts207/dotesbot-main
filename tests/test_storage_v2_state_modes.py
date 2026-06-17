import sqlite3
import pytest
from storage_v2 import StorageV2

def test_daily_budget_isolated_by_mode(tmp_path):
    db_path = str(tmp_path / "state.sqlite")
    storage = StorageV2(db_path)
    
    dry_state = {"total_submitted_usd": 10.0}
    real_state = {"total_submitted_usd": 50.0}
    
    storage.save_daily_budget("2026-06-17", dry_state, mode="dry_live")
    storage.save_daily_budget("2026-06-17", real_state, mode="real_live")
    
    loaded_dry = storage.load_daily_budget("2026-06-17", mode="dry_live")
    loaded_real = storage.load_daily_budget("2026-06-17", mode="real_live")
    
    assert loaded_dry["total_submitted_usd"] == 10.0
    assert loaded_real["total_submitted_usd"] == 50.0

def test_dry_live_budget_does_not_reduce_real_live_budget(tmp_path):
    db_path = str(tmp_path / "state.sqlite")
    storage = StorageV2(db_path)
    
    storage.save_daily_budget("2026-06-17", {"total_submitted_usd": 100.0}, mode="real_live")
    storage.save_daily_budget("2026-06-17", {"total_submitted_usd": 20.0}, mode="dry_live")
    
    # Check that saving dry_live didn't overwrite the real_live entry
    loaded_real = storage.load_daily_budget("2026-06-17", mode="real_live")
    assert loaded_real["total_submitted_usd"] == 100.0

def test_legacy_date_only_budget_migrates_to_legacy_mode_only(tmp_path):
    db_path = str(tmp_path / "state.sqlite")
    # Manually construct legacy schema
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE daily_budgets (
                date_str TEXT PRIMARY KEY,
                total_submitted_usd REAL,
                total_filled_usd REAL,
                open_positions INTEGER,
                daily_realized_pnl_usd REAL,
                submitted_match_sides TEXT,
                submitted_match_usd TEXT,
                submitted_family_usd TEXT,
                updated_at_ns INTEGER
            )
        """)
        conn.execute(
            "INSERT INTO daily_budgets (date_str, total_submitted_usd) VALUES (?, ?)",
            ("2026-06-17", 99.0)
        )
        
    # Instantiate StorageV2 which should trigger migration
    storage = StorageV2(db_path)
    
    # Ensure it's in legacy mode
    legacy = storage.load_daily_budget("2026-06-17", mode="legacy")
    assert legacy is not None
    assert legacy["total_submitted_usd"] == 99.0

def test_legacy_date_only_budget_not_returned_for_real_live_or_dry_live(tmp_path):
    db_path = str(tmp_path / "state.sqlite")
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE daily_budgets (
                date_str TEXT PRIMARY KEY,
                total_submitted_usd REAL,
                total_filled_usd REAL,
                open_positions INTEGER,
                daily_realized_pnl_usd REAL,
                submitted_match_sides TEXT,
                submitted_match_usd TEXT,
                submitted_family_usd TEXT,
                updated_at_ns INTEGER
            )
        """)
        conn.execute("INSERT INTO daily_budgets (date_str, total_submitted_usd) VALUES (?, ?)", ("2026-06-17", 99.0))
        
    storage = StorageV2(db_path)
    
    # Should not be accessible via dry_live or real_live
    assert storage.load_daily_budget("2026-06-17", mode="dry_live") is None
    assert storage.load_daily_budget("2026-06-17", mode="real_live") is None

def test_live_state_loads_real_and_dry_modes_separately(tmp_path, monkeypatch):
    import live_state
    
    # Mock storage_v2 database path to keep isolation
    db_path = str(tmp_path / "state.sqlite")
    monkeypatch.setattr("storage_v2.DEFAULT_DB_PATH", db_path)
    
    # Save manually via API using the patched default db
    storage = StorageV2(db_path)
    storage.save_daily_budget("2026-06-17", {"total_submitted_usd": 15.0}, mode="dry_live")
    storage.save_daily_budget("2026-06-17", {"total_submitted_usd": 25.0}, mode="real_live")
    
    monkeypatch.setattr("live_state.datetime", type('MockDate', (), {
        'now': lambda *args, **kwargs: type('MockTime', (), {'strftime': lambda self, f: "2026-06-17"})()
    }))
    
    dry = live_state.load_live_state(mode="dry_live")
    assert dry["total_submitted_usd"] == 15.0
    
    real = live_state.load_live_state(mode="real_live")
    assert real["total_submitted_usd"] == 25.0

def test_live_state_save_writes_current_mode(tmp_path, monkeypatch):
    import live_state
    
    db_path = str(tmp_path / "state.sqlite")
    monkeypatch.setattr("storage_v2.DEFAULT_DB_PATH", db_path)
    
    monkeypatch.setattr("live_state.datetime", type('MockDate', (), {
        'now': lambda *args, **kwargs: type('MockTime', (), {'strftime': lambda self, f: "2026-06-17"})()
    }))
    
    # Save using live_state API
    live_state.save_live_state(total_submitted_usd=40.0, total_filled_usd=0.0, open_positions=0, mode="real_live")
    live_state.save_live_state(total_submitted_usd=10.0, total_filled_usd=0.0, open_positions=0, mode="dry_live")
    
    storage = StorageV2(db_path)
    real_data = storage.load_daily_budget("2026-06-17", mode="real_live")
    assert real_data["total_submitted_usd"] == 40.0
    
    dry_data = storage.load_daily_budget("2026-06-17", mode="dry_live")
    assert dry_data["total_submitted_usd"] == 10.0
