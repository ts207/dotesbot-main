import os
import time
import pytest
import pandas as pd
from storage import DSwingExitQualityLogger
from live_position_store import LivePosition

@pytest.fixture
def temp_log(tmp_path):
    log_path = tmp_path / "dswing_exit_quality.csv"
    return str(log_path)

def test_dswing_exit_quality_logger(temp_log):
    logger = DSwingExitQualityLogger(filename=temp_log)
    
    pos = LivePosition(
        position_id="test_pos",
        state="OPEN",
        token_id="tok1",
        opposing_token_id="tok2",
        match_id="match1",
        market_name="Test Market",
        side="YES",
        entry_price=0.5,
        shares=10,
        cost_usd=5.0,
        entry_time_ns=time.time_ns() - 100 * 10**9, # 100s ago
        entry_game_time_sec=600,
        event_type="DSWING",
        expected_move=0.0,
        fair_price=0.6,
        trader_kind="dswing",
        signal_id="sig1",
        backed_direction="radiant",
        strategy_kind="DSWING",
    )
    # Add newly added fields
    pos.entry_p_game = 0.9
    pos.entry_series_fair = 0.65
    pos.entry_edge = 0.15
    pos.entry_current_game_number = 1
    pos.entry_series_score_yes = 0
    pos.entry_series_score_no = 0

    class MockDecision:
        def __init__(self, reason, bid):
            self.should_exit = True
            self.reason = reason
            self.reference_bid = bid

    decision = MockDecision("map_end_convergence", 0.7)
    
    map_end_detected_ns = pos.entry_time_ns + 90 * 10**9 # 90s after entry, 10s before exit
    
    logger.log_dswing_exit_quality(
        position=pos,
        decision=decision,
        map_end_detected_ns=map_end_detected_ns,
        execution_path="test_path"
    )
    
    # Force flush if needed, but CsvLogger worker thread handles it.
    # Since it's a daemon thread, we might need a small sleep or join.
    time.sleep(0.5)
    
    df = pd.read_csv(temp_log)
    assert len(df) == 1
    row = df.iloc[0]
    
    assert row["position_id"] == "test_pos"
    assert row["entry_price"] == 0.5
    assert row["exit_bid"] == 0.7
    assert row["convergence_markout"] == pytest.approx(0.2)
    assert row["captured_edge"] == pytest.approx(0.2)
    assert row["hold_sec"] >= 100
    assert row["exit_delay_sec"] == pytest.approx(10, abs=1) # 10s delay between map end and exit
    assert row["entry_p_game"] == 0.9
    assert row["entry_series_fair"] == 0.65
    assert row["execution_path"] == "test_path"
    assert row["exit_reason"] == "map_end_convergence"

def test_report_script_no_crash(temp_log, monkeypatch):
    from scripts.dswing_exit_quality_report import report
    
    # Case 1: File doesn't exist
    if os.path.exists(temp_log):
        os.remove(temp_log)
    monkeypatch.setattr("scripts.dswing_exit_quality_report.DSWING_QUALITY_CSV", temp_log)
    report() # Should not crash
    
    # Case 2: File exists but empty (header only)
    logger = DSwingExitQualityLogger(filename=temp_log)
    time.sleep(0.1)
    report() # Should not crash
    
    # Case 3: One row
    pos = LivePosition(
        position_id="test_pos", state="OPEN", token_id="t", opposing_token_id="o",
        match_id="m", market_name="M", side="YES", entry_price=0.5, shares=1,
        cost_usd=0.5, entry_time_ns=time.time_ns(), entry_game_time_sec=0,
        event_type="DSWING", expected_move=0, fair_price=0.6,
        trader_kind="dswing"
    )
    pos.entry_p_game = 0.5
    pos.entry_series_fair = 0.5
    pos.entry_edge = 0.05
    
    class MockDecision:
        reason = "test"
        reference_bid = 0.6
    
    logger.log_dswing_exit_quality(position=pos, decision=MockDecision())
    time.sleep(0.5)
    report() # Should not crash
