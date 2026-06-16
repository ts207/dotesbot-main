from __future__ import annotations

import time

from execution_policy import POLICY_VERSION, PolicyInput, evaluate_policy
from live_executor import LiveExecutor
from storage import LiveAttemptLogger


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
        "best_bid": 0.50,
        "best_ask": 0.56,
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
