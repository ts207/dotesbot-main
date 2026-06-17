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
