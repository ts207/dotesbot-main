#!/usr/bin/env python3
"""Write roster-continuity feature placeholders and blocker report.

The current processed player rows contain hero/lane fields but not stable player
account IDs. Roster continuity and stand-in features require account IDs, so this
script creates an explicit blocked artifact instead of fabricating a feature.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", default="data/train_pool/model_b_candidates.csv")
    parser.add_argument("--player-rows", default="data/processed/player_match_rows.csv")
    parser.add_argument("--output", default="data/features/roster_continuity_features.csv")
    parser.add_argument("--report", default="reports/roster_continuity_features_report.json")
    args = parser.parse_args()

    candidates = pd.read_csv(args.candidates)
    player_rows = pd.read_csv(args.player_rows, nrows=5) if Path(args.player_rows).exists() else pd.DataFrame()
    has_account_id = any(col in player_rows.columns for col in ["account_id", "player_id", "steam_id"])

    features = candidates[["market_id", "game_id", "match_id", "series_id", "team_a_id", "team_b_id", "start_ts"]].copy()
    features["roster_continuity_available"] = False
    features["team_a_roster_continuity_90d"] = pd.NA
    features["team_b_roster_continuity_90d"] = pd.NA
    features["roster_continuity_diff"] = pd.NA
    features["standin_risk_diff"] = pd.NA
    features["roster_continuity_blocker"] = "missing_player_account_ids"

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    features.to_csv(output, index=False)

    report = {
        "status": "blocked",
        "feature_group": "roster_continuity",
        "reason": "current_processed_player_rows_missing_stable_player_account_ids",
        "input_player_rows": args.player_rows,
        "player_row_columns_sample": list(player_rows.columns),
        "has_account_id_column": bool(has_account_id),
        "output": args.output,
        "rows": int(len(features)),
        "required_to_unblock": [
            "player account_id or equivalent stable player identifier",
            "team lineup by match",
            "rolling as-of lineup history before game start",
        ],
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
