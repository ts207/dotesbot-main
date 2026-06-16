#!/usr/bin/env python3
"""Write draft synergy/counter feature placeholders and blocker report.

Reliable synergy/counter features need a larger rolling draft/outcome history
with sufficient hero-pair and opposing-hero samples. The current v2 pass records
the blocker explicitly and keeps these features out of alpha modeling.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", default="data/train_pool/model_b_candidates.csv")
    parser.add_argument("--output", default="data/features/draft_synergy_counter_features.csv")
    parser.add_argument("--report", default="reports/draft_synergy_counter_features_report.json")
    args = parser.parse_args()

    candidates = pd.read_csv(args.candidates)
    features = candidates[["market_id", "game_id", "match_id", "series_id", "start_ts"]].copy()
    features["draft_synergy_counter_available"] = False
    features["team_a_synergy_score"] = pd.NA
    features["team_b_synergy_score"] = pd.NA
    features["draft_synergy_diff"] = pd.NA
    features["team_a_counter_score"] = pd.NA
    features["team_b_counter_score"] = pd.NA
    features["draft_counter_diff"] = pd.NA
    features["draft_synergy_counter_blocker"] = "insufficient_rolling_pair_counter_history"

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    features.to_csv(output, index=False)

    report = {
        "status": "planned_not_ready",
        "feature_group": "draft_synergy_counter",
        "reason": "requires_larger_rolling_draft_outcome_history_with_pair_and_counter_sample_counts",
        "output": args.output,
        "rows": int(len(features)),
        "required_to_unblock": [
            "rolling draft/outcome corpus",
            "same-team hero pair sample counts",
            "opposing hero counter sample counts",
            "feature_snapshot_ts before game start",
            "minimum sample thresholds and confidence columns",
        ],
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
