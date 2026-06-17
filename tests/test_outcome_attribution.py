"""Tests for outcome_attribution.py."""
from __future__ import annotations

import pytest
import os
import csv
from unittest.mock import MagicMock

from outcome_attribution import (
    infer_strategy_family,
    normalize_exit_reason,
    closed_position_to_outcome_row,
    load_strategy_outcomes,
    write_strategy_outcomes_csv,
    summarize_strategy_outcomes,
)

def test_infer_strategy_family_value():
    assert infer_strategy_family("VALUE_EDGE") == "VALUE"
    assert infer_strategy_family("VALUE") == "VALUE"
    assert infer_strategy_family(None, "VALUE") == "VALUE"

def test_infer_strategy_family_event_continuation():
    assert infer_strategy_family("EVENT_CONTINUATION_EDGE") == "EVENT"

def test_infer_strategy_family_event_reversal():
    assert infer_strategy_family("EVENT_REVERSAL_EDGE") == "EVENT"

def test_infer_strategy_family_dswing():
    assert infer_strategy_family("DSWING") == "DSWING"

def test_infer_strategy_family_manual():
    assert infer_strategy_family("MANUAL") == "MANUAL"
    assert infer_strategy_family(None, "MANUAL") == "MANUAL"

def test_normalize_exit_reason_unknown():
    assert normalize_exit_reason(None) == "unknown"
    assert normalize_exit_reason("") == "unknown"
    assert normalize_exit_reason("take_profit") == "take_profit"

def test_closed_position_to_outcome_row_preserves_strategy_metadata():
    pos = {
        "position_id": "pos1",
        "match_id": "m1",
        "token_id": "t1",
        "market_name": "Mkt 1",
        "side": "YES",
        "strategy_kind": "VALUE_EDGE",
        "entry_engine": "value",
        "exit_engine": "value_fair_invalidation",
        "hold_policy": "thesis_invalidation",
        "entry_price": 0.5,
        "exit_price": 0.6,
        "shares": 100,
        "cost_usd": 50,
        "pnl_usd": 10,
        "roi": 0.2,
        "entry_time_ns": 1000,
        "exit_time_ns": 2000,
        "hold_sec": 1.0,
        "exit_reason": "take_profit",
        "entry_fair": 0.7,
        "entry_edge": 0.2,
        "entry_ask": 0.5,
    }
    row = closed_position_to_outcome_row(pos, mode="paper")
    
    assert row["position_id"] == "pos1"
    assert row["mode"] == "PAPER"
    assert row["strategy_family"] == "VALUE"
    assert row["strategy_kind"] == "VALUE_EDGE"
    assert row["entry_price"] == 0.5
    assert row["exit_price"] == 0.6
    assert row["pnl_usd"] == 10.0
    assert row["roi"] == 0.2
    assert row["exit_reason"] == "take_profit"
    assert row["entry_fair"] == 0.7
    assert row["entry_edge"] == 0.2

def test_closed_position_to_outcome_row_computes_missing_proceeds_pnl_roi():
    pos = {
        "entry_price": 0.5,
        "exit_price": 0.6,
        "shares": 100,
        "cost_usd": 50,
        "entry_time_ns": 1000,
        "exit_time_ns": 2000,
    }
    row = closed_position_to_outcome_row(pos, mode="paper")
    
    assert row["proceeds_usd"] == 60.0
    assert row["pnl_usd"] == 10.0
    assert row["roi"] == 0.2

def test_closed_position_to_outcome_row_computes_hold_sec_from_ns():
    pos = {
        "entry_time_ns": 1_000_000_000,
        "exit_time_ns": 3_500_000_000,
    }
    row = closed_position_to_outcome_row(pos, mode="live")
    assert row["hold_sec"] == 2.5

def test_dswing_outcome_preserves_p_game_series_fair_and_map_metadata():
    pos = {
        "strategy_kind": "DSWING",
        "entry_p_game": 0.75,
        "entry_series_fair": 0.8,
        "entry_series_score_yes": 1,
        "entry_series_score_no": 0,
        "entry_current_game_number": 2,
        "entry_market_type": "MAP_WINNER",
        "entry_book_age_ms": 150,
    }
    row = closed_position_to_outcome_row(pos, mode="paper")
    
    assert row["strategy_family"] == "DSWING"
    assert row["entry_p_game"] == 0.75
    assert row["entry_series_fair"] == 0.8
    assert row["entry_series_score_yes"] == 1
    assert row["entry_series_score_no"] == 0
    assert row["entry_current_game_number"] == 2
    assert row["entry_market_type"] == "MAP_WINNER"
    assert row["entry_book_age_ms"] == 150

def test_event_reversal_outcome_preserves_event_metadata():
    pos = {
        "strategy_kind": "EVENT_REVERSAL_EDGE",
        "strategy_subtype": "kill",
        "entry_is_reversal": True,
        "entry_actual_event_type": "kill",
        "entry_derived_state_flags": ["is_comeback", "high_volatility"],
    }
    row = closed_position_to_outcome_row(pos, mode="live")
    
    assert row["strategy_family"] == "EVENT"
    assert row["strategy_kind"] == "EVENT_REVERSAL_EDGE"
    assert row["strategy_subtype"] == "kill"
    assert row["entry_actual_event_type"] == "kill"
    assert row["entry_derived_state_flags"] == "high_volatility,is_comeback"

def test_load_strategy_outcomes_reads_paper_and_live_closed_positions():
    storage = MagicMock()
    storage.load_closed_positions.side_effect = [
        [{"position_id": "p1"}], # paper
        [{"position_id": "l1"}], # live
    ]
    
    rows = load_strategy_outcomes(storage, modes=("paper", "live"))
    assert len(rows) == 2
    assert rows[0]["mode"] == "PAPER"
    assert rows[1]["mode"] == "LIVE"

def test_write_strategy_outcomes_csv_writes_stable_header(tmp_path):
    path = tmp_path / "outcomes.csv"
    rows = [
        {"a": 1, "b": 2},
        {"a": 3, "b": 4},
    ]
    write_strategy_outcomes_csv(rows, str(path))
    
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == ["a", "b"]
        data = list(reader)
        assert len(data) == 2
        assert data[0]["a"] == "1"

def test_summarize_strategy_outcomes_groups_by_family_and_kind():
    rows = [
        {"mode": "PAPER", "strategy_family": "VALUE", "strategy_kind": "V1", "pnl_usd": 10, "cost_usd": 100, "roi": 0.1, "hold_sec": 10},
        {"mode": "PAPER", "strategy_family": "VALUE", "strategy_kind": "V1", "pnl_usd": -5, "cost_usd": 100, "roi": -0.05, "hold_sec": 20},
        {"mode": "PAPER", "strategy_family": "EVENT", "strategy_kind": "E1", "pnl_usd": 20, "cost_usd": 100, "roi": 0.2, "hold_sec": 30},
    ]
    summary = summarize_strategy_outcomes(rows)
    assert len(summary) == 2
    
    v1 = next(s for s in summary if s["strategy_kind"] == "V1")
    assert v1["trades"] == 2
    assert v1["wins"] == 1
    assert v1["losses"] == 1
    assert v1["win_rate"] == 0.5
    assert v1["total_pnl_usd"] == 5.0
    assert v1["avg_roi"] == 0.025 # (0.1 - 0.05) / 2
    assert v1["avg_hold_sec"] == 15.0

def test_summarize_strategy_outcomes_handles_empty_input():
    assert summarize_strategy_outcomes([]) == []

def test_summarize_strategy_outcomes_calculates_win_rate_and_pnl_per_dollar():
    rows = [
        {"mode": "LIVE", "strategy_family": "DSWING", "strategy_kind": "D1", "pnl_usd": 50, "cost_usd": 50, "roi": 1.0, "hold_sec": 3600},
    ]
    summary = summarize_strategy_outcomes(rows)
    assert summary[0]["win_rate"] == 1.0
    assert summary[0]["pnl_per_dollar"] == 1.0
