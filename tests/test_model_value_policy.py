import pytest
import time
from execution_policy import PolicyInput, evaluate_policy, PolicyResult
import storage_v2
import config

@pytest.fixture(autouse=True)
def mock_storage_path(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_state.sqlite")
    monkeypatch.setattr(storage_v2, "DEFAULT_DB_PATH", db_path)
    # Enable MODEL_VALUE_EDGE for these tests
    from dataclasses import replace
    new_strat = replace(config.RUNTIME_CONFIG.strategy, model_value_enabled=True)
    new_rc = replace(config.RUNTIME_CONFIG, strategy=new_strat)
    monkeypatch.setattr(config, "RUNTIME_CONFIG", new_rc)
    monkeypatch.setattr(config, "MODEL_VALUE_ENABLED", True)
    return db_path

def make_policy_input(**overrides):
    now_ns = time.time_ns()
    base = {
        "mode": "dry_live",
        "strategy_kind": "MODEL_VALUE_EDGE",
        "market_type": "MAP_WINNER",
        "token_id": "TOK1",
        "side": "YES",
        "signal": {
            "event_type": "MODEL_VALUE_EDGE",
            "strategy_kind": "MODEL_VALUE_EDGE",
            "token_id": "TOK1",
            "side": "YES",
            "fair_price": 0.70,
            "executable_edge": 0.15,
            "max_fill_price": 0.95,
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
            "best_ask": 0.54,
            "received_at_ns": now_ns,
        },
        "now_ns": now_ns,
        "risk_state": {
            "total_submitted_usd": 0,
            "open_positions": 0,
            "daily_realized_pnl_usd": 0,
            "daily_drawdown_usd": 0,
            "submitted_family_usd": 0,
        }
    }
    # Deep merge overrides
    for k, v in overrides.items():
        if isinstance(v, dict) and k in base:
            base[k].update(v)
        else:
            base[k] = v
            
    return PolicyInput(**base)

def test_policy_passes_clean_input():
    inp = make_policy_input()
    res = evaluate_policy(inp)
    assert res.allowed is True
    assert res.reason == "allowed"

def test_policy_blocks_wide_spread(monkeypatch):
    # Set MAX_SPREAD to 0.05
    monkeypatch.setattr(config, "MAX_SPREAD", 0.05)
    # Book spread is 0.06 (0.56 - 0.50)
    inp = make_policy_input(book={"best_bid": 0.50, "best_ask": 0.56})
    res = evaluate_policy(inp)
    assert res.allowed is False
    assert "spread_too_wide" in res.reason

def test_policy_blocks_stale_book(monkeypatch):
    monkeypatch.setattr(config, "MAX_BOOK_AGE_MS", 1000)
    now_ns = time.time_ns()
    # Book received 2 seconds ago
    inp = make_policy_input(
        book={"best_bid": 0.50, "best_ask": 0.54, "received_at_ns": now_ns - 2 * 1_000_000_000},
        now_ns=now_ns
    )
    res = evaluate_policy(inp)
    assert res.allowed is False
    assert "book_stale" in res.reason

def test_policy_blocks_terminal_ask_if_policy_requires():
    # If ask >= 0.95, it's blocked by terminal_price_chase
    inp = make_policy_input(
        book={"best_bid": 0.94, "best_ask": 0.96},
        signal={"max_fill_price": 0.99}
    )
    res = evaluate_policy(inp)
    assert res.allowed is False
    assert "terminal_price_chase" in res.reason

def test_policy_real_live_fails_closed_by_default(monkeypatch):
    # Real live fails closed unless contract explicitly enables real live
    inp = make_policy_input(mode="real_live")
    res = evaluate_policy(inp)
    assert res.allowed is False
    assert "strategy_contract_disabled" in res.reason
