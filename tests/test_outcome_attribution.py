from __future__ import annotations

import csv
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from outcome_attribution import (
    OUTCOME_FIELDNAMES,
    apply_settlement_counterfactual,
    closed_position_to_outcome_row,
    infer_strategy_family,
    load_strategy_outcomes,
    summarize_exit_reasons,
    summarize_strategy_outcomes,
    write_strategy_outcomes_csv,
)
from storage import StrategySignalLogger


def base_position(**overrides):
    pos = {
        "position_id": "pos1",
        "match_id": "match1",
        "token_id": "token1",
        "market_name": "Team A vs Team B",
        "side": "YES",
        "signal_id": "sig1",
        "strategy_kind": "VALUE_EDGE",
        "strategy_subtype": "leader_value",
        "entry_engine": "value_engine",
        "exit_engine": "live_exit_engine",
        "hold_policy": "hold_to_settle",
        "edge_type": "fair_minus_ask",
        "target_horizon": "settlement",
        "expected_hold_sec": 1800,
        "entry_price": 0.50,
        "exit_price": 0.60,
        "shares": 100.0,
        "cost_usd": 50.0,
        "proceeds_usd": 60.0,
        "pnl_usd": 10.0,
        "roi": 0.20,
        "hold_sec": 120.0,
        "entry_time_ns": 1_000_000_000,
        "exit_time_ns": 121_000_000_000,
        "entry_game_time_sec": 800,
        "exit_game_time_sec": 920,
        "exit_reason": "settled",
        "entry_fair": 0.72,
        "entry_edge": 0.22,
        "entry_ask": 0.50,
        "entry_backed_side": "radiant",
        "entry_radiant_lead": 5000,
        "entry_actual_event_type": "VALUE",
        "entry_derived_state_flags": ["high_conviction", "map_winner"],
        "policy_allowed": True,
        "policy_reason": "ok",
        "policy_version": "v1",
        "risk_tags": ["value"],
        "would_pass_live": True,
        "live_skip_reason": "",
        "paper_only_bypass": False,
    }
    pos.update(overrides)
    return pos


def test_closed_position_to_outcome_row_preserves_strategy_metadata():
    row = closed_position_to_outcome_row(base_position(), mode="paper")

    assert row["position_id"] == "pos1"
    assert row["mode"] == "paper"
    assert row["strategy_family"] == "VALUE"
    assert row["strategy_kind"] == "VALUE_EDGE"
    assert row["strategy_subtype"] == "leader_value"
    assert row["signal_id"] == "sig1"
    assert row["entry_engine"] == "value_engine"
    assert row["exit_engine"] == "live_exit_engine"
    assert row["hold_policy"] == "hold_to_settle"
    assert row["edge_type"] == "fair_minus_ask"
    assert row["target_horizon"] == "settlement"
    assert row["expected_hold_sec"] == 1800
    assert row["entry_derived_state_flags"] == "high_conviction,map_winner"
    assert row["risk_tags"] == "value"


def test_closed_position_to_outcome_row_computes_missing_proceeds_pnl_roi():
    row = closed_position_to_outcome_row(
        base_position(proceeds_usd=None, pnl_usd=None, roi=None),
        mode="paper",
    )

    assert row["proceeds_usd"] == 60.0
    assert row["pnl_usd"] == 10.0
    assert row["roi"] == 0.2


def test_closed_position_to_outcome_row_computes_hold_sec_from_ns():
    row = closed_position_to_outcome_row(
        base_position(hold_sec=None, entry_time_ns=1_000_000_000, exit_time_ns=3_500_000_000),
        mode="live",
    )

    assert row["hold_sec"] == 2.5


def test_infer_strategy_family_value():
    assert infer_strategy_family("VALUE") == "VALUE"
    assert infer_strategy_family("VALUE_EDGE") == "VALUE"


def test_infer_strategy_family_event_continuation():
    assert infer_strategy_family("EVENT_CONTINUATION_EDGE") == "EVENT"


def test_infer_strategy_family_event_reversal():
    assert infer_strategy_family("EVENT_REVERSAL_EDGE") == "EVENT"


def test_infer_strategy_family_dswing():
    assert infer_strategy_family("DSWING") == "DSWING"


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("EVENT_TRIGGERED_VALUE", "EVENT"),
        ("BOOK_MOVE_ALPHA", "BOOK_MOVE"),
        ("MANUAL", "MANUAL"),
        ("unknown", None),
    ],
)
def test_infer_strategy_family_other_rules(kind, expected):
    assert infer_strategy_family(kind) == expected


def test_settlement_counterfactual_winner():
    row = closed_position_to_outcome_row(base_position(), mode="paper", settlement_price=1.0)

    assert row["settlement_status"] == "known"
    assert row["settlement_pnl_usd"] == 50.0
    assert row["active_exit_delta_usd"] == -40.0
    assert row["active_exit_delta_roi"] == -0.8
    assert row["exit_helped"] is False


def test_settlement_counterfactual_loser():
    row = closed_position_to_outcome_row(base_position(), mode="paper", settlement_price=0.0)

    assert row["settlement_pnl_usd"] == -50.0
    assert row["active_exit_delta_usd"] == 60.0
    assert row["exit_helped"] is True


def test_settlement_counterfactual_unknown():
    row = closed_position_to_outcome_row(base_position(), mode="paper")

    assert row["settlement_status"] == "unknown"
    assert row["settlement_price"] is None
    assert row["settlement_pnl_usd"] is None
    assert row["active_exit_delta_usd"] is None
    assert row["exit_helped"] is None


def test_apply_settlement_counterfactual_unknown():
    row = apply_settlement_counterfactual({"shares": 10, "cost_usd": 5, "pnl_usd": 1}, None)
    assert row["settlement_status"] == "unknown"


def test_catastrophe_salvage_negative_active_exit_delta():
    pos = base_position(
        exit_reason="catastrophe_salvage",
        exit_price=0.12,
        proceeds_usd=None,
        pnl_usd=None,
        roi=None,
    )

    row = closed_position_to_outcome_row(pos, mode="paper", settlement_price=1.0)

    assert row["exit_reason"] == "catastrophe_salvage"
    assert row["pnl_usd"] == -38.0
    assert row["settlement_pnl_usd"] == 50.0
    assert row["active_exit_delta_usd"] == -88.0
    assert row["exit_helped"] is False


def test_summarize_strategy_outcomes_groups_by_family_and_kind():
    rows = [
        closed_position_to_outcome_row(base_position(strategy_kind="VALUE_EDGE", pnl_usd=10.0, roi=0.1), mode="paper", settlement_price=1.0),
        closed_position_to_outcome_row(base_position(position_id="pos2", strategy_kind="VALUE_EDGE", pnl_usd=-5.0, roi=-0.05), mode="paper", settlement_price=0.0),
        closed_position_to_outcome_row(base_position(position_id="pos3", strategy_kind="DSWING", pnl_usd=20.0, roi=0.2), mode="paper", settlement_price=1.0),
    ]

    summary = summarize_strategy_outcomes(rows)

    value = next(row for row in summary if row["strategy_family"] == "VALUE")
    assert value["trades"] == 2
    assert value["wins"] == 1
    assert value["losses"] == 1
    assert value["win_rate"] == 0.5
    assert value["actual_pnl_usd"] == 5.0
    assert value["settlement_pnl_usd"] == 0.0
    assert value["active_exit_delta_usd"] == 5.0
    assert value["avg_active_exit_delta_usd"] == 2.5
    assert value["avg_roi"] == pytest.approx(0.025)


def test_summarize_exit_reasons_quantifies_exit_help_rate():
    rows = [
        closed_position_to_outcome_row(base_position(position_id="a", exit_reason="catastrophe_salvage", pnl_usd=-38.0), mode="paper", settlement_price=1.0),
        closed_position_to_outcome_row(base_position(position_id="b", exit_reason="catastrophe_salvage", pnl_usd=5.0), mode="paper", settlement_price=0.0),
        closed_position_to_outcome_row(base_position(position_id="c", exit_reason="fair_invalidation", pnl_usd=8.0), mode="paper"),
    ]

    summary = summarize_exit_reasons(rows)

    salvage = next(row for row in summary if row["exit_reason"] == "catastrophe_salvage")
    assert salvage["trades"] == 2
    assert salvage["wins"] == 1
    assert salvage["actual_pnl_usd"] == -33.0
    assert salvage["settlement_pnl_usd"] == 0.0
    assert salvage["active_exit_delta_usd"] == -33.0
    assert salvage["avg_active_exit_delta_usd"] == -16.5
    assert salvage["exit_help_rate"] == 0.5


def test_load_strategy_outcomes_reads_paper_and_live_closed_positions():
    storage = MagicMock()
    storage.load_closed_positions.side_effect = [
        [base_position(position_id="paper1", match_id="m1")],
        [base_position(position_id="live1", match_id="m2")],
    ]

    rows = load_strategy_outcomes(
        storage,
        modes=("paper", "live"),
        settlement_by_match={"m1": {"default": 1.0}, "m2": {"token1": 0.0}},
    )

    assert [row["mode"] for row in rows] == ["paper", "live"]
    assert rows[0]["settlement_price"] == 1.0
    assert rows[1]["settlement_price"] == 0.0
    assert storage.load_closed_positions.call_args_list[0].args == ("paper",)
    assert storage.load_closed_positions.call_args_list[1].args == ("live",)


def test_write_strategy_outcomes_csv_stable_header(tmp_path):
    path = tmp_path / "strategy_outcomes.csv"
    rows = [closed_position_to_outcome_row(base_position(), mode="paper")]

    write_strategy_outcomes_csv(rows, str(path))

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == OUTCOME_FIELDNAMES
        assert list(reader)[0]["position_id"] == "pos1"


def test_dswing_outcome_preserves_p_game_series_fair_and_map_metadata():
    row = closed_position_to_outcome_row(
        base_position(
            strategy_kind="DSWING",
            entry_p_game=0.97,
            entry_series_fair=0.88,
            entry_series_score_yes=1,
            entry_series_score_no=0,
            entry_current_game_number=2,
            entry_market_type="MATCH_WINNER",
            entry_book_age_ms=350,
        ),
        mode="paper",
    )

    assert row["strategy_family"] == "DSWING"
    assert row["entry_p_game"] == 0.97
    assert row["entry_series_fair"] == 0.88
    assert row["entry_series_score_yes"] == 1
    assert row["entry_series_score_no"] == 0
    assert row["entry_current_game_number"] == 2
    assert row["entry_market_type"] == "MATCH_WINNER"
    assert row["entry_book_age_ms"] == 350


def test_event_reversal_outcome_preserves_event_metadata():
    row = closed_position_to_outcome_row(
        base_position(
            strategy_kind="EVENT_REVERSAL_EDGE",
            strategy_subtype="kill_reversal",
            entry_actual_event_type="KILL",
            entry_derived_state_flags=["comeback", "swing"],
        ),
        mode="paper",
    )

    assert row["strategy_family"] == "EVENT"
    assert row["strategy_kind"] == "EVENT_REVERSAL_EDGE"
    assert row["strategy_subtype"] == "kill_reversal"
    assert row["entry_actual_event_type"] == "KILL"
    assert row["entry_derived_state_flags"] == "comeback,swing"


def _flush_logger(logger: StrategySignalLogger) -> None:
    logger._queue.join()
    logger._stop_event.set()


def test_strategy_signal_logger_log_reject_accepts_value_reject_shape(tmp_path, monkeypatch):
    monkeypatch.setattr("storage._mirror_state", lambda *args, **kwargs: None)
    logger = StrategySignalLogger(filename=str(tmp_path / "strategy_signals.csv"))
    rej = SimpleNamespace(
        match_id="m1",
        received_at_ns=time.time_ns(),
        reason="lead_too_small",
        direction="radiant",
        side="YES",
        token_id="t1",
        fair_price=0.6,
        ask=0.5,
        edge=0.1,
    )

    logger.log_reject(rej, strategy="VALUE")
    _flush_logger(logger)

    with open(logger.filename, newline="", encoding="utf-8") as f:
        row = list(csv.DictReader(f))[0]
    assert row["strategy"] == "VALUE"
    assert row["reject_reason"] == "lead_too_small"
    assert row["event_id"] == ""


def test_strategy_signal_logger_log_reject_accepts_dswing_reject_shape(tmp_path, monkeypatch):
    monkeypatch.setattr("storage._mirror_state", lambda *args, **kwargs: None)
    logger = StrategySignalLogger(filename=str(tmp_path / "strategy_signals.csv"))
    rej = SimpleNamespace(match_id="m1", reason="missing_series_state_or_model")

    logger.log_reject(rej, strategy="DSWING")
    _flush_logger(logger)

    with open(logger.filename, newline="", encoding="utf-8") as f:
        row = list(csv.DictReader(f))[0]
    assert row["strategy"] == "DSWING"
    assert row["reject_reason"] == "missing_series_state_or_model"
    assert row["received_at_ns"] == ""


def test_strategy_signal_logger_log_reject_accepts_sparse_reject_shape(tmp_path, monkeypatch):
    monkeypatch.setattr("storage._mirror_state", lambda *args, **kwargs: None)
    logger = StrategySignalLogger(filename=str(tmp_path / "strategy_signals.csv"))

    logger.log_reject(SimpleNamespace(reason="sparse"))
    _flush_logger(logger)

    with open(logger.filename, newline="", encoding="utf-8") as f:
        row = list(csv.DictReader(f))[0]
    assert row["reject_reason"] == "sparse"
    assert row["match_id"] == ""


def test_strategy_signal_logger_log_reject_handles_missing_received_at_ns(tmp_path, monkeypatch):
    monkeypatch.setattr("storage._mirror_state", lambda *args, **kwargs: None)
    logger = StrategySignalLogger(filename=str(tmp_path / "strategy_signals.csv"))

    logger.log_reject(SimpleNamespace(match_id="m1", reason="no_ts"))
    _flush_logger(logger)

    with open(logger.filename, newline="", encoding="utf-8") as f:
        row = list(csv.DictReader(f))[0]
    assert row["timestamp_utc"]
    assert row["received_at_ns"] == ""
