import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "analyze_gettoplive_structure_state.py"
SPEC = importlib.util.spec_from_file_location("analyze_gettoplive_structure_state", SCRIPT_PATH)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def test_building_only_top_live_change_is_research_only():
    rows = [
        {
            "data_source": "top_live",
            "received_at_ns": "1000",
            "match_id": "M1",
            "game_time_sec": "100",
            "radiant_lead": "1000",
            "radiant_score": "1",
            "dire_score": "0",
            "building_state": "4784201",
            "tower_state": str((1 << 22) - 1),
        },
        {
            "data_source": "top_live",
            "received_at_ns": "2000",
            "match_id": "M1",
            "game_time_sec": "130",
            "radiant_lead": "1200",
            "radiant_score": "1",
            "dire_score": "0",
            "building_state": "4784200",
            "tower_state": str((1 << 22) - 1),
        },
    ]

    report = audit.summarize_rows(rows)
    totals = report["totals"]
    assert totals["top_live_rows"] == 2
    assert totals["building_state_changes"] == 1
    assert totals["building_change_without_tower_change"] == 1
    assert totals["valid_tower_deltas"] == 0
    assert report["interpretation"]["research_only"] == "raw TopLive building_state rax/base/T4 interpretation"
