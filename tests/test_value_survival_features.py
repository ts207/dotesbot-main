import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "analyze_value_survival_features.py"
SPEC = importlib.util.spec_from_file_location("analyze_value_survival_features", SCRIPT_PATH)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def test_bucketize_leader_aligned_score_and_tower_context():
    rows = [
        {
            "_pnl_usd": 10.0,
            "_won": 1,
            "stake_usd": "20",
            "snapshot_joined": True,
            "leader_kill_diff": 3,
            "leader_tower_diff": 2,
            "leader_enemy_towers_down": 4,
            "leader_own_towers_down": 1,
        },
        {
            "_pnl_usd": -20.0,
            "_won": 0,
            "stake_usd": "20",
            "snapshot_joined": True,
            "leader_kill_diff": -2,
            "leader_tower_diff": -1,
            "leader_enemy_towers_down": 1,
            "leader_own_towers_down": 3,
        },
    ]

    buckets = audit.bucketize(rows)
    assert buckets["score_and_tower:aligned"]["trades"] == 1
    assert buckets["score_and_tower:aligned"]["roi_pct"] == 50.0
    assert buckets["score_and_tower:not_aligned"]["losses"] == 1
    assert buckets["enemy_towers_down:>=3"]["wins"] == 1
    assert buckets["own_towers_down:>=3"]["losses"] == 1

    rec = audit.gate_recommendation(buckets, audit.summarize_group(rows))
    assert rec["live_gate_change"] == "none"
