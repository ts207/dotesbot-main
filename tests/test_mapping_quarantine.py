from __future__ import annotations

import yaml

from mapping import load_valid_mappings
from mapping_audit import audit_mappings, quarantine_critical_issues
from mapping_quarantine import is_quarantined, quarantine_in_file, quarantine_mapping
from runtime.bot_runtime import _audit_and_quarantine_mappings


def _mapping(**overrides):
    data = {
        "name": "A vs B Game 1",
        "market_type": "MAP_WINNER",
        "yes_team": "Team A",
        "no_team": "Team B",
        "yes_token_id": "YES",
        "no_token_id": "NO",
        "dota_match_id": "M1",
        "confidence": 1.0,
        "steam_radiant_team": "Team A",
        "steam_dire_team": "Team B",
        "steam_side_mapping": "normal",
    }
    data.update(overrides)
    return data


def test_quarantined_mapping_is_not_valid(tmp_path):
    path = tmp_path / "markets.yaml"
    mapping = quarantine_mapping(_mapping(), "orientation_flip_suspected")
    path.write_text(yaml.dump({"markets": [mapping]}))

    valid, errors = load_valid_mappings(str(path))

    assert valid == []
    assert errors
    assert "mapping_quarantined" in errors[0].reason


def test_mapping_audit_flags_orientation_flip_and_quarantines():
    mapping = _mapping()
    issues = audit_mappings(
        [mapping],
        games_by_match_id={"M1": {"radiant_lead": 7000}},
        books_by_yes_token={"YES": {"best_ask": 0.20}},
    )

    assert any(issue.reason.startswith("orientation_flip_suspected") for issue in issues)
    assert quarantine_critical_issues([mapping], issues) == 1
    assert is_quarantined(mapping)


def test_quarantine_in_file_marks_matching_market(tmp_path):
    path = tmp_path / "markets.yaml"
    path.write_text(yaml.dump({"markets": [_mapping(market_id="MID")]}))

    assert quarantine_in_file(path, "MID", "unit_test")
    data = yaml.safe_load(path.read_text())

    assert data["markets"][0]["mapping_state"] == "quarantined"
    assert data["markets"][0]["quarantine_reason"] == "unit_test"


def test_runtime_mapping_refresh_quarantines_critical_orientation_issue():
    mapping = _mapping()

    changed = _audit_and_quarantine_mappings(
        [mapping],
        [{"match_id": "M1", "radiant_lead": 7000}],
        books_by_yes_token={"YES": {"best_ask": 0.20}},
    )

    assert changed == 1
    assert mapping["mapping_state"] == "quarantined"
