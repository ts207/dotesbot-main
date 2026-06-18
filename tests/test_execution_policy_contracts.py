import pytest
from execution_policy import _strategy_disabled, PolicyInput
import strategy_registry
import yaml
from pathlib import Path

def test_strategy_contract_enforcement(tmp_path, monkeypatch):
    # Mock strategy_registry directory
    d = tmp_path / "strategies"
    d.mkdir()
    monkeypatch.setattr(strategy_registry, "STRATEGY_DIR", d)
    
    # Create a mock contract
    contract = """
strategy_kind: MOCK_STRATEGY
version: "1.0"
enabled_paper: true
enabled_dry_live: false
enabled_real_live: false
edge_type: executable
target_horizon: settlement
entry_trigger: poll
exit_trigger: null
primary_metric: roi
promotion_rule: positive
disable_rule: none
"""
    (d / "mock_strategy.yaml").write_text(contract)
    strategy_registry.get.cache_clear()
    
    inp = PolicyInput(
        mode="paper_research",
        strategy_kind="MOCK_STRATEGY",
        market_type="MATCH_WINNER",
        token_id="1", side="YES", signal={}, game={}, mapping={}, book={}, now_ns=0
    )
    
    # Paper is enabled, so it should not be rejected by contract
    res = _strategy_disabled(inp)
    # Could still be rejected by RUNTIME_CONFIG if MOCK_STRATEGY is mapped there, but it's not.
    assert res is None
    
    # Dry live is disabled
    inp_dry = PolicyInput(
        mode="dry_live",
        strategy_kind="MOCK_STRATEGY",
        market_type="MATCH_WINNER",
        token_id="1", side="YES", signal={}, game={}, mapping={}, book={}, now_ns=0
    )
    res_dry = _strategy_disabled(inp_dry)
    assert res_dry == "strategy_contract_disabled:MOCK_STRATEGY:dry_live"

    # Real live is disabled
    inp_real = PolicyInput(
        mode="real_live",
        strategy_kind="MOCK_STRATEGY",
        market_type="MATCH_WINNER",
        token_id="1", side="YES", signal={}, game={}, mapping={}, book={}, now_ns=0
    )
    res_real = _strategy_disabled(inp_real)
    assert res_real == "strategy_contract_disabled:MOCK_STRATEGY:real_live"

