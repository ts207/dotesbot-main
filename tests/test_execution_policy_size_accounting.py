from __future__ import annotations

from unittest.mock import MagicMock
from execution_policy import evaluate_policy, PolicyInput

def _policy_input(
    signal_overrides: dict | None = None,
    book_overrides: dict | None = None,
    risk_overrides: dict | None = None,
    game_overrides: dict | None = None,
) -> PolicyInput:
    sig = {
        "decision": "live_buy_yes",
        "market_name": "Match Winner",
        "side": "YES",
        "executable_edge": 0.05,
        "lag": 1.0,
        "fair_price": 0.55,
        "max_fill_price": 0.52,
        "size_usd": 0.0,
        "strategy_family": "VALUE",
        "event_type": "VALUE"
    }
    if signal_overrides:
        sig.update(signal_overrides)

    import time
    now_ns = time.time_ns()
    book = {"best_ask": 0.50, "best_bid": 0.40, "best_ask_size": 100, "received_at_ns": now_ns}
    if book_overrides:
        book.update(book_overrides)

    risk = {
        "total_submitted_usd": 0.0,
        "daily_realized_pnl_usd": 0.0,
        "match_open_usd": 0.0,
        "VALUE_max_live_usd": 100.0,
        "submitted_family_usd": {"VALUE": 0.0},
    }
    if risk_overrides:
        risk.update(risk_overrides)

    game = {"radiant_lead": 0, "source_update_age_sec": 1.0, "received_at_ns": now_ns}
    if game_overrides:
        game.update(game_overrides)

    map_dict = {"yes_token_id": "tok_yes", "no_token_id": "tok_no"}
    return PolicyInput(
        signal=sig,
        mapping=map_dict,
        book=book,
        risk_state=risk,
        game=game,
        now_ns=now_ns,
        mode="dry_live",
        strategy_kind="VALUE_EDGE",
        market_type="MAP_WINNER",
        token_id="tok_yes",
        side="YES"
    )

def test_family_cap_counts_size_usd():
    inp = _policy_input(
        signal_overrides={"size_usd": 6.0, "strategy_family": "EVENT"},
        risk_overrides={
            "EVENT_max_live_usd": 10.0,
            "submitted_family_usd": {"EVENT": 5.0},
        }
    )
    result = evaluate_policy(inp)
    assert not result.allowed
    assert "strategy_family_cap:EVENT:used=5.0_cap=10.0" in result.reason

def test_family_cap_counts_target_size_usd():
    inp = _policy_input(
        signal_overrides={"target_size_usd": 6.0, "strategy_family": "EVENT"},
        risk_overrides={
            "EVENT_max_live_usd": 10.0,
            "submitted_family_usd": {"EVENT": 5.0},
        }
    )
    result = evaluate_policy(inp)
    assert not result.allowed
    assert "strategy_family_cap:EVENT:used=5.0_cap=10.0" in result.reason

def test_family_cap_counts_sized_usd():
    inp = _policy_input(
        signal_overrides={"sized_usd": 6.0, "strategy_family": "EVENT"},
        risk_overrides={
            "EVENT_max_live_usd": 10.0,
            "submitted_family_usd": {"EVENT": 5.0},
        }
    )
    result = evaluate_policy(inp)
    assert not result.allowed
    assert "strategy_family_cap:EVENT:used=5.0_cap=10.0" in result.reason

def test_value_match_cap_counts_target_size_usd():
    # VALUE match cap is VALUE_MAX_PER_MATCH (which is 6 by default)
    from config import VALUE_MAX_PER_MATCH
    inp = _policy_input(
        signal_overrides={"target_size_usd": 4.0, "strategy_family": "VALUE"},
        risk_overrides={"match_open_usd": VALUE_MAX_PER_MATCH - 2.0}
    )
    result = evaluate_policy(inp)
    assert not result.allowed
    assert "value_match_cap:used=" in result.reason

def test_value_match_cap_counts_sized_usd():
    from config import VALUE_MAX_PER_MATCH
    inp = _policy_input(
        signal_overrides={"sized_usd": 4.0, "strategy_family": "VALUE"},
        risk_overrides={"match_open_usd": VALUE_MAX_PER_MATCH - 2.0}
    )
    result = evaluate_policy(inp)
    assert not result.allowed
    assert "value_match_cap:used=" in result.reason

def test_requested_size_precedence_prefers_size_usd_then_target_then_sized():
    # If size_usd is set, it ignores target_size_usd
    inp = _policy_input(
        signal_overrides={
            "size_usd": 2.0,           # Allows it (5+2 = 7 <= 10)
            "target_size_usd": 10.0,   # Would fail
            "sized_usd": 10.0,         # Would fail
            "strategy_family": "EVENT"
        },
        risk_overrides={
            "EVENT_max_live_usd": 10.0,
            "submitted_family_usd": {"EVENT": 5.0},
        }
    )
    result = evaluate_policy(inp)
    assert result.allowed

    # If size_usd not set but target_size_usd is set
    inp2 = _policy_input(
        signal_overrides={
            "target_size_usd": 2.0,    # Allows it
            "sized_usd": 10.0,         # Would fail
            "strategy_family": "EVENT"
        },
        risk_overrides={
            "EVENT_max_live_usd": 10.0,
            "submitted_family_usd": {"EVENT": 5.0},
        }
    )
    result2 = evaluate_policy(inp2)
    assert result2.allowed
