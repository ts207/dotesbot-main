from __future__ import annotations

import time

import execution_policy
from execution_policy import POLICY_VERSION, PolicyInput, evaluate_policy
from live_executor import LiveExecutor
from storage import LiveAttemptLogger
import storage_v2
import pytest

@pytest.fixture(autouse=True)
def mock_storage_path(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_state.sqlite")
    monkeypatch.setattr(storage_v2, "DEFAULT_DB_PATH", db_path)
    import config
    monkeypatch.setattr(config, "TRADE_EVENTS", {"POLL_FIGHT_SWING", "POLL_DECISIVE_STOMP", "VALUE"})
    return db_path


def _policy_input(**overrides):
    now_ns = time.time_ns()
    base = {
        "mode": "dry_live",
        "strategy_kind": "POLL_FIGHT_SWING",
        "market_type": "MAP_WINNER",
        "token_id": "TOK1",
        "side": "YES",
        "signal": {
            "event_type": "POLL_FIGHT_SWING",
            "token_id": "TOK1",
            "side": "YES",
            "fair_price": 0.70,
            "executable_edge": 0.10,
            "lag": 0.10,
            "max_fill_price": 0.80,
            "event_schema_version": "cadence_v1",
            "source_cadence_quality": "direct",
            "event_quality": 1.0,
        },
        "game": {
            "match_id": "M1",
            "data_source": "top_live",
            "received_at_ns": now_ns,
        },
        "mapping": {
            "market_type": "MAP_WINNER",
            "yes_token_id": "TOK1",
            "no_token_id": "TOK2",
        },
        "book": {
            "best_bid": 0.50,
            "best_ask": 0.56,
            "received_at_ns": now_ns,
        },
        "now_ns": now_ns,
        "risk_state": {
            "total_submitted_usd": 0,
            "open_positions": 0,
            "daily_realized_pnl_usd": 0,
            "match_open_usd": 0,
        },
    }
    base.update(overrides)
    return PolicyInput(**base)


def test_execution_policy_allows_clean_live_candidate():
    result = evaluate_policy(_policy_input())

    assert result.allowed is True
    assert result.would_pass_live is True
    assert result.policy_version == POLICY_VERSION


def test_execution_policy_rejects_stale_book():
    now_ns = time.time_ns()
    old_book = {
        "best_ask": 0.50,
        "best_bid": 0.40,
        "ask_size": 100,
        "received_at_ns": now_ns - 120_000_000_000,
    }

    result = evaluate_policy(_policy_input(now_ns=now_ns, book=old_book))

    assert result.allowed is False
    assert result.live_skip_reason.startswith("book_stale:")
    assert result.risk_tags == ("book_stale",)


def test_live_executor_reject_attempt_carries_policy_fields():
    attempt = LiveExecutor()._reject({}, {}, {}, "unit_test_reject")

    assert attempt.policy_allowed is False
    assert attempt.policy_reason == "unit_test_reject"
    assert attempt.would_pass_live is False
    assert attempt.live_skip_reason == "unit_test_reject"
    assert attempt.policy_version == POLICY_VERSION


def test_live_attempt_logger_has_policy_columns(tmp_path):
    logger = LiveAttemptLogger(filename=str(tmp_path / "live_attempts.csv"))

    assert "policy_allowed" in logger.headers
    assert "policy_reason" in logger.headers
    assert "would_pass_live" in logger.headers
    assert "live_skip_reason" in logger.headers
    assert "paper_only_bypass" in logger.headers
    assert "policy_version" in logger.headers
    assert "risk_tags" in logger.headers

def test_execution_policy_rejects_stale_steam():
    now_ns = time.time_ns()
    game = {"data_source": "top_live", "received_at_ns": now_ns - 120_000_000_000}
    result = evaluate_policy(_policy_input(now_ns=now_ns, game=game))
    assert result.allowed is False
    assert "steam_stale" in result.live_skip_reason

def test_execution_policy_orientation_flip():
    inp = _policy_input()
    inp.game["radiant_lead"] = 10000
    inp.mapping["steam_side_mapping"] = "normal"
    inp.mapping["yes_token_id"] = "TOK1"
    inp.book["best_ask"] = 0.20 # Unreasonably low for a huge lead
    result = evaluate_policy(inp)
    assert result.allowed is False
    assert "orientation_flip_suspected" in result.live_skip_reason

def test_execution_policy_rejects_wide_spread():
    inp = _policy_input()
    inp.book["best_bid"] = 0.10
    inp.book["best_ask"] = 0.90
    result = evaluate_policy(inp)
    assert result.allowed is False
    assert "spread_too_wide" in result.live_skip_reason

def test_execution_policy_rejects_low_decisive_stomp_quality():
    inp = _policy_input()
    inp.signal["event_type"] = "POLL_DECISIVE_STOMP"
    inp.signal["event_quality"] = 0.1 # Very low quality
    inp.book["best_ask"] = 0.70 # above the 0.65 floor
    inp.book["best_bid"] = 0.60
    result = evaluate_policy(inp)
    assert result.allowed is False
    assert "decisive_stomp_quality_too_low" in result.live_skip_reason

def test_execution_policy_rejects_low_decisive_stomp_price_floor():
    inp = _policy_input()
    inp.signal["event_type"] = "POLL_DECISIVE_STOMP"
    inp.signal["event_quality"] = 1.0 
    inp.book["best_ask"] = 0.60 # below the 0.65 floor
    inp.book["best_bid"] = 0.50
    result = evaluate_policy(inp)
    assert result.allowed is False
    assert "decisive_stomp_price_below_floor" in result.live_skip_reason

def test_execution_policy_max_positions():
    inp = _policy_input()
    inp.risk_state["open_positions"] = 1000 # way above max
    result = evaluate_policy(inp)
    assert result.allowed is False
    assert "max_open_positions" in result.live_skip_reason

def test_execution_policy_max_drawdown():
    inp = _policy_input()
    inp.risk_state["daily_realized_pnl_usd"] = -1000 # busted
    result = evaluate_policy(inp)
    assert result.allowed is False
    assert "daily_drawdown_breaker" in result.live_skip_reason

def test_execution_policy_double_match_entry_blocked_for_event():
    inp = _policy_input()
    inp.signal["strategy_family"] = "EVENT"
    inp.signal["event_direction"] = "YES"
    inp.risk_state["submitted_match_sides"] = ["YES"]
    result = evaluate_policy(inp)
    assert result.allowed is False
    assert "match_already_submitted" in result.live_skip_reason

def test_execution_policy_double_match_entry_allowed_for_value():
    inp = _policy_input()
    inp.signal["strategy_family"] = "VALUE"
    inp.signal["event_type"] = "VALUE"
    inp.signal["event_direction"] = "YES"
    inp.risk_state["submitted_match_sides"] = ["YES"]
    result = evaluate_policy(inp)
    assert result.allowed is True

def test_execution_policy_family_cap():
    inp = _policy_input()
    inp.signal["strategy_family"] = "EVENT"
    inp.signal["size_usd"] = 10
    inp.risk_state["EVENT_max_live_usd"] = 50
    inp.risk_state["submitted_family_usd"] = {"EVENT": 45}
    result = evaluate_policy(inp)
    assert result.allowed is False
    assert "strategy_family_cap" in result.live_skip_reason


def test_execution_policy_dswing_lag_bypass():
    inp = _policy_input(strategy_kind="DSWING")
    inp.signal["strategy_kind"] = "DSWING"
    inp.signal["strategy_family"] = "DSWING"
    inp.signal["target_horizon"] = "map_end"
    inp.signal["expected_hold_sec"] = 0
    inp.signal["lag"] = None  # DSWING has no lag
    result = evaluate_policy(inp)
    assert result.allowed is True
    assert "hold_to_settle_edge_lag_bypass" in result.risk_tags
