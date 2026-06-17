import pytest
from exit_observation import build_exit_observation_row

def test_observation_row_metadata():
    pos = {
        "token_id": "tok1",
        "match_id": "m1",
        "side": "YES",
        "entry_price": 0.5,
        "shares": 100,
        "cost_usd": 50,
        "entry_time_ns": 1000,
        "strategy_family": "VALUE",
        "strategy_kind": "VALUE_EDGE",
        "hold_policy": "thesis_invalidation"
    }
    row = build_exit_observation_row(
        position=pos,
        book={"best_bid": 0.55, "best_ask": 0.57},
        now_ns=2000
    )
    assert row["token_id"] == "tok1"
    assert row["current_bid"] == 0.55
    assert row["age_sec"] == (2000 - 1000) / 1e9
    assert row["strategy_family"] == "VALUE"
    assert row["actual_exit_reason"] is None

def test_observation_row_catastrophe_triggered():
    from exit_observation import build_exit_observation_row
    pos = {
        "token_id": "tok1",
        "match_id": "m1",
        "entry_price": 0.5,
        "entry_time_ns": 1000,
        "backed_direction": "radiant"
    }
    # Bid below floor (0.12) and radiant losing (-2000 NW)
    row = build_exit_observation_row(
        position=pos,
        book={"best_bid": 0.10},
        game={"radiant_lead": -2500},
        now_ns=2000
    )
    assert row["catastrophe_salvage_triggered"] is True

def test_observation_row_fair_invalidation_triggered():
    from exit_observation import build_exit_observation_row
    pos = {
        "token_id": "tok1",
        "match_id": "m1",
        "entry_price": 0.5,
        "entry_time_ns": 1000,
        "fair_price": 0.45 # current_fair
    }
    # Current fair (0.45) < entry (0.5) - 0.03 (buffer) AND current fair < bid (0.55) - 0.05 (buffer)
    # 0.45 < 0.47 AND 0.45 < 0.50 -> True
    row = build_exit_observation_row(
        position=pos,
        book={"best_bid": 0.55},
        now_ns=2000
    )
    assert row["fair_invalidation_triggered"] is True

def test_observation_row_game_over_triggered():
    from exit_observation import build_exit_observation_row
    pos = {"match_id": "m1", "entry_time_ns": 1000}
    row = build_exit_observation_row(
        position=pos,
        book={},
        game_over_match_ids={"m1"},
        now_ns=2000
    )
    assert row["game_over_triggered"] is True
    assert row["map_end_convergence_triggered"] is True

def test_observation_row_max_hold_triggered():
    from exit_observation import build_exit_observation_row
    import config
    # Max hold is 48 hours by default
    max_hold_ns = config.MAX_HOLD_HOURS * 3600 * 1_000_000_000
    pos = {"entry_time_ns": 1000}
    row = build_exit_observation_row(
        position=pos,
        book={},
        now_ns=1000 + max_hold_ns + 1000
    )
    assert row["max_hold_triggered"] is True

def test_write_exit_observation_stable_header(tmp_path):
    from exit_observation import write_exit_observation
    csv_path = tmp_path / "obs.csv"
    row1 = {"col1": "val1", "col2": "val2"}
    row2 = {"col1": "val3", "col2": "val4"}
    
    write_exit_observation(row1, path=str(csv_path))
    write_exit_observation(row2, path=str(csv_path))
    
    with open(csv_path, "r") as f:
        lines = f.readlines()
        assert len(lines) == 3 # Header + 2 rows
        assert "col1,col2" in lines[0]
        assert "val1,val2" in lines[1]
        assert "val3,val4" in lines[2]
