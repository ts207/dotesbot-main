"""Tests for hardened runtime market-state isolation (overlay)."""
from __future__ import annotations

import os
import yaml
import pytest
import json
from pathlib import Path
from unittest.mock import patch

from mapping import load_mappings, RUNTIME_MARKETS_PATH, DEFAULT_MARKETS_PATH
from sync_markets import load_markets, write_markets
from discover_markets import main as discover_main


@pytest.fixture
def mock_markets(tmp_path, monkeypatch):
    # Setup temp directory structure
    base_file = tmp_path / "markets.yaml"
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    runtime_file = logs_dir / "runtime_markets.yaml"
    
    # Mock paths in the modules
    monkeypatch.setattr("mapping.DEFAULT_MARKETS_PATH", str(base_file))
    monkeypatch.setattr("mapping.RUNTIME_MARKETS_PATH", str(runtime_file))
    monkeypatch.setattr("sync_markets.MARKETS_YAML", str(base_file))
    monkeypatch.setattr("sync_markets.RUNTIME_MARKETS_PATH", str(runtime_file))
    monkeypatch.setattr("discover_markets.MARKETS_YAML", str(base_file))
    monkeypatch.setattr("discover_markets.RUNTIME_MARKETS_PATH", str(runtime_file))
    
    base_data = {
        "markets": [
            {
                "name": "Base Market",
                "condition_id": "cond1",
                "yes_token_id": "tok1",
                "no_token_id": "tok2",
                "dota_match_id": "STEAM_MATCH_OR_LOBBY_ID_HERE",
                "confidence": 0.0,
                "yes_team": "Team A",
                "market_id": "m1",
            }
        ]
    }
    with open(base_file, "w") as f:
        yaml.dump(base_data, f)
        
    return base_file, runtime_file


def test_load_mappings_no_overlay(mock_markets):
    base_file, runtime_file = mock_markets
    markets = load_mappings()
    assert len(markets) == 1
    assert markets[0]["condition_id"] == "cond1"
    assert markets[0]["confidence"] == 0.0


def test_load_mappings_with_overlay(mock_markets):
    base_file, runtime_file = mock_markets
    
    runtime_data = {
        "markets": [
            {
                "condition_id": "cond1",
                "dota_match_id": "12345",
                "confidence": 1.0,
            }
        ]
    }
    with open(runtime_file, "w") as f:
        yaml.dump(runtime_data, f)
        
    markets = load_mappings()
    assert len(markets) == 1
    assert markets[0]["condition_id"] == "cond1"
    assert markets[0]["dota_match_id"] == "12345"
    assert markets[0]["confidence"] == 1.0


def test_load_mappings_adds_new_markets_from_overlay(mock_markets):
    base_file, runtime_file = mock_markets
    
    runtime_data = {
        "markets": [
            {
                "name": "New Market",
                "condition_id": "cond2",
                "yes_token_id": "tok3",
                "no_token_id": "tok4",
                "dota_match_id": "67890",
                "confidence": 1.0,
            }
        ]
    }
    with open(runtime_file, "w") as f:
        yaml.dump(runtime_data, f)
        
    markets = load_mappings()
    assert len(markets) == 2
    ids = {m["condition_id"] for m in markets}
    assert ids == {"cond1", "cond2"}


def test_sync_markets_load_returns_merged(mock_markets):
    base_file, runtime_file = mock_markets
    
    runtime_data = {
        "markets": [
            {
                "condition_id": "cond1",
                "dota_match_id": "12345",
                "confidence": 1.0,
            }
        ]
    }
    with open(runtime_file, "w") as f:
        yaml.dump(runtime_data, f)
        
    mdata = load_markets()
    markets = mdata["markets"]
    assert len(markets) == 1
    assert markets[0]["dota_match_id"] == "12345"


def test_sync_markets_write_only_mutates_runtime_file(mock_markets):
    base_file, runtime_file = mock_markets
    
    mdata = load_markets()
    markets = mdata["markets"]
    markets[0]["dota_match_id"] = "modified"
    
    write_markets(mdata)
    
    # Base file should remain unchanged
    with open(base_file) as f:
        base_reloaded = yaml.safe_load(f)
        assert base_reloaded["markets"][0]["dota_match_id"] == "STEAM_MATCH_OR_LOBBY_ID_HERE"
        
    # Runtime file should have the modification
    assert os.path.exists(runtime_file)
    with open(runtime_file) as f:
        runtime_reloaded = yaml.safe_load(f)
        assert runtime_reloaded["markets"][0]["dota_match_id"] == "modified"


@pytest.mark.asyncio
async def test_discover_markets_uses_runtime_path(mock_markets, monkeypatch):
    base_file, runtime_file = mock_markets
    
    # Mock Gamma fetch to return one new valid market
    async def mock_fetch_active_markets(session):
        return [
            {
                "id": "new1",
                "conditionId": "cond_new",
                "question": "Dota 2: Team A vs Team B - Game 3 Winner",
                "outcomes": json.dumps(["Team A", "Team B"]),
                "clobTokenIds": json.dumps(["tok5", "tok6"]),
                "active": True,
                "closed": False,
            }
        ]
    
    # Mock fallback to return empty list
    async def mock_fallback(session):
        return []
    
    monkeypatch.setattr("discover_markets.fetch_active_markets", mock_fetch_active_markets)
    monkeypatch.setattr("discover_markets.fetch_polymarket_dota_page_markets", mock_fallback)
    
    await discover_main(auto_write=True, output_path=str(runtime_file))
    
    # Base file should NOT have the new market
    with open(base_file) as f:
        base_data = yaml.safe_load(f)
        assert len(base_data["markets"]) == 1
        
    # Runtime file SHOULD have the new market
    assert os.path.exists(runtime_file)
    with open(runtime_file) as f:
        runtime_data = yaml.safe_load(f)
        assert any(m["condition_id"] == "cond_new" for m in runtime_data["markets"])


# ── BATCH 8 HARDENING TESTS ──────────────────────────────────────────────────

def test_write_markets_is_atomic_and_removes_temp_file(mock_markets):
    base_file, runtime_file = mock_markets
    data = {"markets": [{"condition_id": "c1"}]}
    
    # Verify write works
    write_markets(data)
    assert os.path.exists(runtime_file)
    
    # Verify no temp files left behind
    temp_files = list(runtime_file.parent.glob(".*.tmp"))
    assert len(temp_files) == 0


def test_write_markets_creates_parent_directory(tmp_path, monkeypatch):
    deep_path = tmp_path / "new_dir" / "deeper" / "runtime.yaml"
    # sync_markets uses mapping.RUNTIME_MARKETS_PATH as default
    monkeypatch.setattr("mapping.RUNTIME_MARKETS_PATH", str(deep_path))
    monkeypatch.setattr("sync_markets.RUNTIME_MARKETS_PATH", str(deep_path))
    
    write_markets({"markets": []})
    assert deep_path.exists()


def test_corrupt_runtime_overlay_returns_base_unchanged(mock_markets):
    base_file, runtime_file = mock_markets
    
    # Write invalid YAML
    with open(runtime_file, "w") as f:
        f.write("markets: [")
        
    # Should not crash and return base
    markets = load_mappings()
    assert len(markets) == 1
    assert markets[0]["condition_id"] == "cond1"
    assert markets[0]["confidence"] == 0.0


def test_non_list_runtime_markets_ignored(mock_markets):
    base_file, runtime_file = mock_markets
    
    runtime_data = {"markets": {"not": "a list"}}
    with open(runtime_file, "w") as f:
        yaml.dump(runtime_data, f)
        
    markets = load_mappings()
    assert len(markets) == 1
    assert markets[0]["confidence"] == 0.0


def test_non_dict_runtime_market_entries_skipped(mock_markets):
    base_file, runtime_file = mock_markets
    
    runtime_data = {"markets": ["not a dict", {"condition_id": "cond1", "confidence": 1.0}]}
    with open(runtime_file, "w") as f:
        yaml.dump(runtime_data, f)
        
    markets = load_mappings()
    assert len(markets) == 1
    assert markets[0]["confidence"] == 1.0


def test_overlay_does_not_override_canonical_seed_fields(mock_markets):
    base_file, runtime_file = mock_markets
    
    runtime_data = {
        "markets": [
            {
                "condition_id": "cond1",
                "yes_team": "Bad Override",
                "market_id": "bad_id",
                "confidence": 1.0,
            }
        ]
    }
    with open(runtime_file, "w") as f:
        yaml.dump(runtime_data, f)
        
    markets = load_mappings()
    m = markets[0]
    assert m["yes_team"] == "Team A" # Preserved
    assert m["market_id"] == "m1"     # Preserved
    assert m["confidence"] == 1.0    # Allowed override
