import os
import csv
import pytest
from unittest.mock import patch, MagicMock
from exit_observation_enrichment import enrich_exit_observations, load_settlement_outcomes

@pytest.fixture
def dummy_csvs(tmp_path):
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"
    
    with open(input_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["token_id", "shares", "cost_usd", "actual_pnl_usd", "actual_exit_reason"])
        writer.writeheader()
        # Row 1: winning, exited early for profit but less than holding to end
        writer.writerow({"token_id": "tok_win", "shares": "10", "cost_usd": "5", "actual_pnl_usd": "2", "actual_exit_reason": "early_exit"})
        # Row 2: losing, but catastrophe salvage saved some money
        writer.writerow({"token_id": "tok_lose", "shares": "10", "cost_usd": "5", "actual_pnl_usd": "-3", "actual_exit_reason": "catastrophe_salvage"})
        # Row 3: unknown settlement
        writer.writerow({"token_id": "tok_unk", "shares": "10", "cost_usd": "5", "actual_pnl_usd": "1", "actual_exit_reason": "early_exit"})
        
    return str(input_path), str(output_path)

@patch("exit_observation_enrichment.load_settlement_outcomes")
def test_enrich_exit_observation_winning_settlement(mock_load, dummy_csvs):
    in_csv, out_csv = dummy_csvs
    mock_load.return_value = {"tok_win": 1.0, "tok_lose": 0.0}
    
    enrich_exit_observations(in_csv, out_csv)
    
    with open(out_csv, "r") as f:
        reader = list(csv.DictReader(f))
        
    row = reader[0]
    assert row["token_id"] == "tok_win"
    assert row["settlement_status"] == "resolved"
    assert row["settlement_price"] == "1.0"
    # settlement_pnl = 10 * 1.0 - 5 = 5.0
    assert float(row["settlement_pnl_usd"]) == 5.0
    # active_exit_delta = 2 - 5.0 = -3.0
    assert float(row["active_exit_delta_usd"]) == -3.0
    assert row["exit_helped"] == "False"

@patch("exit_observation_enrichment.load_settlement_outcomes")
def test_enrich_exit_observation_losing_settlement(mock_load, dummy_csvs):
    in_csv, out_csv = dummy_csvs
    mock_load.return_value = {"tok_win": 1.0, "tok_lose": 0.0}
    
    enrich_exit_observations(in_csv, out_csv)
    
    with open(out_csv, "r") as f:
        reader = list(csv.DictReader(f))
        
    row = reader[1]
    assert row["token_id"] == "tok_lose"
    assert row["settlement_status"] == "resolved"
    assert row["settlement_price"] == "0.0"
    # settlement_pnl = 10 * 0.0 - 5 = -5.0
    assert float(row["settlement_pnl_usd"]) == -5.0
    # active_exit_delta = -3 - (-5.0) = 2.0
    assert float(row["active_exit_delta_usd"]) == 2.0
    assert row["exit_helped"] == "True"

@patch("exit_observation_enrichment.load_settlement_outcomes")
def test_enrich_unknown_settlement_keeps_null_fields(mock_load, dummy_csvs):
    in_csv, out_csv = dummy_csvs
    mock_load.return_value = {"tok_win": 1.0, "tok_lose": 0.0}
    
    enrich_exit_observations(in_csv, out_csv)
    
    with open(out_csv, "r") as f:
        reader = list(csv.DictReader(f))
        
    row = reader[2]
    assert row["token_id"] == "tok_unk"
    assert row["settlement_status"] == "unknown"
    assert row["settlement_price"] == ""
    assert row["settlement_pnl_usd"] == ""
    assert row["active_exit_delta_usd"] == ""
    assert row["exit_helped"] == ""

@patch("exit_observation_enrichment.load_settlement_outcomes")
def test_catastrophe_salvage_negative_delta_detected(mock_load, dummy_csvs):
    # What if catastrophe salvage actually exited a winning trade? 
    # (i.e. it would have been 1.0, but we exited for -3.0 pnl)
    in_csv, out_csv = dummy_csvs
    mock_load.return_value = {"tok_lose": 1.0} # Actually it won!
    
    enrich_exit_observations(in_csv, out_csv)
    
    with open(out_csv, "r") as f:
        reader = list(csv.DictReader(f))
        
    row = reader[1]
    assert row["token_id"] == "tok_lose"
    assert row["actual_exit_reason"] == "catastrophe_salvage"
    # settlement_pnl = 10 * 1.0 - 5 = 5.0
    # actual_pnl = -3.0
    # delta = -3.0 - 5.0 = -8.0
    assert float(row["active_exit_delta_usd"]) == -8.0
    assert row["exit_helped"] == "False"

@patch("exit_observation_enrichment.load_settlement_outcomes")
def test_enrichment_preserves_original_columns(mock_load, dummy_csvs):
    in_csv, out_csv = dummy_csvs
    mock_load.return_value = {}
    
    enrich_exit_observations(in_csv, out_csv)
    
    with open(out_csv, "r") as f:
        reader = list(csv.DictReader(f))
        
    row = reader[0]
    assert "shares" in row
    assert "cost_usd" in row
    assert "actual_pnl_usd" in row
    assert row["shares"] == "10"

@patch("exit_observation_enrichment.load_settlement_outcomes")
def test_enrichment_writes_stable_header(mock_load, dummy_csvs):
    in_csv, out_csv = dummy_csvs
    mock_load.return_value = {}
    
    enrich_exit_observations(in_csv, out_csv)
    
    with open(out_csv, "r") as f:
        headers = f.readline().strip().split(",")
        
    expected_new = ["settlement_price", "settlement_pnl_usd", "active_exit_delta_usd", "active_exit_delta_roi", "exit_helped", "settlement_status"]
    for f in expected_new:
        assert f in headers
