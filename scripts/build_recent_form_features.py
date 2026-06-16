#!/usr/bin/env python3
"""Extract recent-form features from the rolling team-strength artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


RECENT_FORM_COLUMNS = [
    "market_id",
    "game_id",
    "match_id",
    "series_id",
    "team_a_id",
    "team_b_id",
    "start_ts",
    "feature_snapshot_ts",
    "team_a_match_count_30d",
    "team_b_match_count_30d",
    "team_a_rolling_winrate_30d",
    "team_b_rolling_winrate_30d",
    "team_recent_form_diff",
    "team_strength_feature_confidence",
    "no_leak_valid",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team-strength", default="data/features/team_strength_features.csv")
    parser.add_argument("--output", default="data/features/recent_form_features.csv")
    parser.add_argument("--report", default="reports/recent_form_features_report.json")
    args = parser.parse_args()

    team_strength = pd.read_csv(args.team_strength)
    cols = [col for col in RECENT_FORM_COLUMNS if col in team_strength.columns]
    recent = team_strength[cols].copy()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    recent.to_csv(output, index=False)

    report = {
        "status": "ok",
        "feature_group": "recent_form",
        "input": args.team_strength,
        "output": args.output,
        "rows": int(len(recent)),
        "team_recent_form_diff_non_null": int(recent["team_recent_form_diff"].notna().sum())
        if "team_recent_form_diff" in recent
        else 0,
        "no_leak_violations": int((~recent["no_leak_valid"].astype(bool)).sum())
        if "no_leak_valid" in recent
        else None,
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
