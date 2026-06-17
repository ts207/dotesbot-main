from __future__ import annotations

from risk_config_audit import audit_risk_config, config_from_env


def test_risk_config_audit_warns_on_current_small_account_oversized_rails():
    audit = audit_risk_config(
        48.85,
        {
            "MAX_TRADE_USD": 20,
            "VALUE_MAX_PER_MATCH": 20,
            "MAX_TOTAL_LIVE_USD": 200,
            "MAX_DAILY_DRAWDOWN_USD": 10,
            "MAX_OPEN_POSITIONS": 15,
        },
    )

    assert not audit["ok"]
    keys = {finding["key"] for finding in audit["findings"]}
    assert "MAX_TRADE_USD" in keys
    assert "VALUE_MAX_PER_MATCH" in keys
    assert "MAX_TOTAL_LIVE_USD" in keys
    assert "MAX_DAILY_DRAWDOWN_USD" in keys
    assert "MAX_OPEN_POSITIONS" in keys


def test_risk_config_audit_accepts_batch_12_suggested_rails():
    audit = audit_risk_config(
        48.85,
        {
            "MAX_TRADE_USD": 5,
            "VALUE_MAX_PER_MATCH": 6,
            "MAX_TOTAL_LIVE_USD": 15,
            "MAX_DAILY_DRAWDOWN_USD": 5,
            "MAX_OPEN_POSITIONS": 2,
        },
    )

    assert audit["ok"]
    assert audit["findings"] == []


def test_config_from_env_reads_expected_keys():
    env = {
        "MAX_TRADE_USD": "5",
        "VALUE_MAX_PER_MATCH": "6",
        "MAX_TOTAL_LIVE_USD": "15",
        "MAX_DAILY_DRAWDOWN_USD": "5",
        "MAX_OPEN_POSITIONS": "2",
        "OTHER": "ignored",
    }

    assert config_from_env(env) == {
        "MAX_TRADE_USD": "5",
        "VALUE_MAX_PER_MATCH": "6",
        "MAX_TOTAL_LIVE_USD": "15",
        "MAX_DAILY_DRAWDOWN_USD": "5",
        "MAX_OPEN_POSITIONS": "2",
    }
