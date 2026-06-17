from __future__ import annotations

import sqlite3

from live_position_store import LivePosition, LivePositionStore
from state_store import StateStore


def _pos(position_id: str = "P1") -> LivePosition:
    return LivePosition(
        position_id=position_id,
        state="OPEN",
        token_id="TOK1",
        opposing_token_id="TOK2",
        match_id="M1",
        market_name="Market",
        side="YES",
        entry_price=0.5,
        shares=10,
        cost_usd=5,
        entry_time_ns=1,
        entry_game_time_sec=600,
        event_type="VALUE",
        expected_move=0,
        fair_price=0.7,
        strategy_kind="VALUE_EDGE",
        strategy_family="VALUE",
    )


def test_state_store_initializes_required_tables(tmp_path):
    db = tmp_path / "state.sqlite"
    StateStore(str(db))

    with sqlite3.connect(db) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    assert "live_positions" in tables
    assert "live_orders" in tables
    assert "policy_decisions" in tables
    assert "feed_health" in tables

def test_live_position_store_writes_to_storage_v2(tmp_path, monkeypatch):
    db_path = str(tmp_path / "state.sqlite")
    monkeypatch.setattr("storage_v2.DEFAULT_DB_PATH", db_path)
    
    store = LivePositionStore()
    store.positions["P1"] = _pos()
    store.save()

    from storage_v2 import StorageV2
    storage = StorageV2(db_path)
    rows = storage.load_positions(mode="live", active_only=True)

    assert len(rows) == 1
    assert rows[0]["position_id"] == "P1"
    assert rows[0]["strategy_kind"] == "VALUE_EDGE"

def test_state_store_records_order_policy_strategy_allocation_and_mapping(tmp_path):
    db = tmp_path / "state.sqlite"
    store = StateStore(str(db))

    store.record_live_attempt(
        {
            "timestamp_utc": "2026-06-16T00:00:00+00:00",
            "phase": "submit",
            "order_id": "OID1",
            "order_status": "filled",
            "match_id": "M1",
            "token_id": "YES",
            "policy_allowed": True,
            "policy_reason": "allowed",
            "policy_version": "execution_policy_v1",
        }
    )
    store.record_strategy_signal(
        {
            "signal_id": "S1",
            "match_id": "M1",
            "token_id": "YES",
            "strategy": "VALUE_EDGE",
        }
    )
    store.record_allocation_decision(
        {
            "timestamp_utc": "2026-06-16T00:00:01+00:00",
            "match_id": "M1",
            "token_id": "YES",
            "winner_strategy": "VALUE_EDGE",
        }
    )
    store.record_mapping_snapshots(
        [
            {
                "market_id": "MID",
                "condition_id": "CID",
                "dota_match_id": "M1",
                "mapping_state": "quarantined",
            }
        ]
    )

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM live_orders").fetchone()[0] == 1
        assert conn.execute("SELECT allowed, reason FROM policy_decisions").fetchone() == (1, "allowed")
        assert conn.execute("SELECT strategy_kind FROM strategy_signals").fetchone()[0] == "VALUE_EDGE"
        assert conn.execute("SELECT strategy_kind FROM allocation_decisions").fetchone()[0] == "VALUE_EDGE"
        assert conn.execute("SELECT mapping_state FROM mapping_snapshots").fetchone()[0] == "quarantined"


def test_record_mapping_snapshots_handles_duplicate_condition_ids(tmp_path):
    db = tmp_path / "state.sqlite"
    store = StateStore(str(db))

    store.record_mapping_snapshots(
        [
            {
                "market_id": "MID",
                "condition_id": "CID",
                "dota_match_id": "8854333124",
                "mapping_state": "bound",
            },
            {
                "market_id": "MID",
                "condition_id": "CID",
                "dota_match_id": "8854333124",
                "mapping_state": "bound",
            },
        ]
    )

    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT snapshot_id FROM mapping_snapshots").fetchall()

    assert len(rows) == 2
    assert len({row[0] for row in rows}) == 2


def test_record_mapping_snapshots_does_not_raise_on_duplicate_rows(tmp_path):
    db = tmp_path / "state.sqlite"
    store = StateStore(str(db))
    duplicate_rows = [
        {
            "market_id": "",
            "condition_id": "CID",
            "dota_match_id": "8854333124",
            "mapping_state": "quarantined",
        },
        {
            "market_id": "",
            "condition_id": "CID",
            "dota_match_id": "8854333124",
            "mapping_state": "quarantined",
        },
    ]

    store.record_mapping_snapshots(duplicate_rows)
    store.record_mapping_snapshots(duplicate_rows)

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM mapping_snapshots").fetchone()[0] == 4
