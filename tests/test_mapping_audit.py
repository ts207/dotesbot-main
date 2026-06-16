import pytest
from datetime import datetime, timezone, timedelta
from mapping_audit import audit_mappings, MappingAuditIssue, _is_active

def test_audit_duplicate_yes_no_token():
    mappings = [{
        "name": "Test Market",
        "market_type": "MAP_WINNER",
        "yes_token_id": "SAME_TOKEN",
        "no_token_id": "SAME_TOKEN"
    }]
    issues = audit_mappings(mappings)
    assert any(i.reason == "duplicate yes/no token" for i in issues)

def test_audit_placeholder_active_mapping():
    mappings = [{
        "name": "Test Market",
        "market_type": "MAP_WINNER",
        "dota_match_id": "MATCH_OR_LOBBY_ID_HERE",
        "confidence": 1.0,
        "yes_token_id": "T1",
        "no_token_id": "T2",
        "steam_radiant_team": "A",
        "steam_dire_team": "B",
        "yes_team": "A",
        "no_team": "B"
    }]
    issues = audit_mappings(mappings)
    assert any("placeholder" in i.reason.lower() or "not a valid integer" in i.reason.lower() for i in issues)

def test_audit_side_mismatches_normal():
    mappings = [{
        "name": "Test Market",
        "market_type": "MAP_WINNER",
        "confidence": 1.0,
        "dota_match_id": "123",
        "yes_token_id": "T1",
        "no_token_id": "T2",
        "steam_radiant_team": "A",
        "steam_dire_team": "B",
        "yes_team": "C", # Mismatch
        "no_team": "B",
        "steam_side_mapping": "normal"
    }]
    issues = audit_mappings(mappings)
    assert any(i.reason == "team_name_mismatch:normal_side_mapping" for i in issues)

def test_audit_side_mismatches_reversed():
    mappings = [{
        "name": "Test Market",
        "market_type": "MAP_WINNER",
        "confidence": 1.0,
        "dota_match_id": "123",
        "yes_token_id": "T1",
        "no_token_id": "T2",
        "steam_radiant_team": "A",
        "steam_dire_team": "B",
        "yes_team": "A", # Mismatch, should be B since reversed
        "no_team": "B",
        "steam_side_mapping": "reversed"
    }]
    issues = audit_mappings(mappings)
    assert any(i.reason == "team_name_mismatch:reversed_side_mapping" for i in issues)

def test_audit_match_winner_non_decider():
    mappings = [{
        "name": "Test Market",
        "market_type": "MATCH_WINNER",
        "confidence": 1.0,
        "dota_match_id": "123",
        "yes_token_id": "T1",
        "no_token_id": "T2",
        "steam_radiant_team": "A",
        "steam_dire_team": "B",
        "yes_team": "A",
        "no_team": "B",
        "steam_side_mapping": "normal",
        "current_game_number": 2,
        "treat_as_map_winner": True
    }]
    issues = audit_mappings(mappings)
    assert any(i.reason == "MATCH_WINNER non-decider incorrectly treated as MAP_WINNER" for i in issues)

def test_audit_stale_scheduled_start():
    old_time = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    mappings = [{
        "name": "Test Market",
        "market_type": "MAP_WINNER",
        "confidence": 1.0,
        "dota_match_id": "123",
        "yes_token_id": "T1",
        "no_token_id": "T2",
        "steam_radiant_team": "A",
        "steam_dire_team": "B",
        "yes_team": "A",
        "no_team": "B",
        "steam_side_mapping": "normal",
        "scheduled_start_utc": old_time
    }]
    issues = audit_mappings(mappings)
    assert any(i.reason == "scheduled_start_utc stale" for i in issues)

def test_audit_valid_resolution_multiple_markets():
    mappings = [
        {
            "name": "Mkt 1",
            "market_type": "MAP_WINNER",
            "confidence": 1.0,
            "dota_match_id": "123",
            "yes_token_id": "T1",
            "no_token_id": "T2",
            "steam_radiant_team": "A",
            "steam_dire_team": "B",
            "yes_team": "A",
            "no_team": "B",
            "steam_side_mapping": "normal",
            "scheduled_start_utc": datetime.now(timezone.utc).isoformat()
        },
        {
            "name": "Mkt 2",
            "market_type": "MAP_WINNER",
            "confidence": 1.0,
            "dota_match_id": "456",
            "yes_token_id": "T3",
            "no_token_id": "T4",
            "steam_radiant_team": "C",
            "steam_dire_team": "D",
            "yes_team": "D",
            "no_team": "C",
            "steam_side_mapping": "reversed",
            "scheduled_start_utc": datetime.now(timezone.utc).isoformat()
        }
    ]
    issues = audit_mappings(mappings)
    assert len(issues) == 0

def test_is_active_check():
    assert not _is_active({"confidence": 0.5})
    assert not _is_active({"confidence": 1.0, "dota_match_id": "MATCH_OR_LOBBY_ID_HERE"})
    assert _is_active({"confidence": 1.0, "dota_match_id": "123"})
