#!/usr/bin/env python3
"""Build Model B v2 candidate rows by adding feature artifacts to v1 rows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


TEAM_STRENGTH_COLUMNS = [
    "team_a_match_count_30d",
    "team_b_match_count_30d",
    "team_a_rolling_winrate_30d",
    "team_b_rolling_winrate_30d",
    "team_winrate_30d_diff",
    "team_a_match_count_90d",
    "team_b_match_count_90d",
    "team_a_rolling_winrate_90d",
    "team_b_rolling_winrate_90d",
    "team_winrate_90d_diff",
    "team_a_match_count_180d",
    "team_b_match_count_180d",
    "team_a_rolling_winrate_180d",
    "team_b_rolling_winrate_180d",
    "team_winrate_180d_diff",
    "team_a_match_count_365d",
    "team_b_match_count_365d",
    "team_a_rolling_winrate_365d",
    "team_b_rolling_winrate_365d",
    "team_winrate_365d_diff",
    "team_strength_diff",
    "team_recent_form_diff",
    "team_match_count_90d_diff",
    "team_a_strength_confidence",
    "team_b_strength_confidence",
    "team_strength_feature_confidence",
    "team_strength_missing_reason",
    "feature_snapshot_ts",
    "max_history_ts",
    "no_leak_valid",
]

OPTIONAL_FEATURE_FILES = [
    ("roster", "data/features/roster_continuity_features.csv"),
    ("draft_synergy_counter", "data/features/draft_synergy_counter_features.csv"),
]


def load_optional(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def merge_feature(base: pd.DataFrame, features: pd.DataFrame, prefix: str = "") -> pd.DataFrame:
    if features.empty or "market_id" not in features.columns:
        return base
    keep = ["market_id"] + [col for col in features.columns if col != "market_id" and col not in base.columns]
    renamed = features[keep].copy()
    if prefix:
        rename_map = {
            col: f"{prefix}_{col}"
            for col in renamed.columns
            if col != "market_id" and col in {"feature_snapshot_ts", "no_leak_valid"}
        }
        renamed = renamed.rename(columns=rename_map)
    return base.merge(renamed, on="market_id", how="left")


def value_counts(series: pd.Series) -> dict[str, int]:
    return {str(k): int(v) for k, v in series.fillna("missing").value_counts().to_dict().items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="data/train_pool/model_b_candidates.csv")
    parser.add_argument("--team-strength", default="data/features/team_strength_features.csv")
    parser.add_argument("--output", default="data/train_pool/model_b_v2_candidates.csv")
    parser.add_argument("--summary", default="data/train_pool/model_b_v2_summary.json")
    args = parser.parse_args()

    base = pd.read_csv(args.base)
    out = base.copy()

    team_strength = load_optional(Path(args.team_strength))
    if not team_strength.empty:
        keep = ["market_id"] + [col for col in TEAM_STRENGTH_COLUMNS if col in team_strength.columns]
        out = out.merge(team_strength[keep], on="market_id", how="left")

    for prefix, path in OPTIONAL_FEATURE_FILES:
        out = merge_feature(out, load_optional(Path(path)), prefix=prefix)

    out["v2_team_strength_feature_available"] = out["team_strength_diff"].notna() if "team_strength_diff" in out else False
    out["v2_team_strength_confidence_ge_0_5"] = (
        pd.to_numeric(out.get("team_strength_feature_confidence"), errors="coerce").fillna(0) >= 0.5
    )
    out["v2_team_strength_confidence_ge_0_8"] = (
        pd.to_numeric(out.get("team_strength_feature_confidence"), errors="coerce").fillna(0) >= 0.8
    )
    out["v2_no_leak_valid"] = out.get("no_leak_valid", True)
    if not isinstance(out["v2_no_leak_valid"], pd.Series):
        out["v2_no_leak_valid"] = True

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output, index=False)

    prob_ready = out[out.get("analysis_ready_probability", False).astype(bool)].copy()
    summary: dict[str, Any] = {
        "status": "ok",
        "model_b_v1_status": "stopped_no_trade",
        "model_b_v2_status": "feature_data_build_only",
        "training_allowed": False,
        "reason_training_not_allowed": "do_not_train_until_non_locked_probability_ready_ge_500_and_validation_rows_ge_125",
        "base_rows": int(len(base)),
        "v2_rows": int(len(out)),
        "probability_ready_rows": int(len(prob_ready)),
        "team_strength_feature_available_rows": int(out["v2_team_strength_feature_available"].sum()),
        "team_strength_confidence_ge_0_5_rows": int(out["v2_team_strength_confidence_ge_0_5"].sum()),
        "team_strength_confidence_ge_0_8_rows": int(out["v2_team_strength_confidence_ge_0_8"].sum()),
        "probability_ready_team_strength_available_rows": int(prob_ready["v2_team_strength_feature_available"].sum())
        if len(prob_ready)
        else 0,
        "probability_ready_team_strength_confidence_ge_0_5_rows": int(
            prob_ready["v2_team_strength_confidence_ge_0_5"].sum()
        )
        if len(prob_ready)
        else 0,
        "probability_ready_team_strength_confidence_ge_0_8_rows": int(
            prob_ready["v2_team_strength_confidence_ge_0_8"].sum()
        )
        if len(prob_ready)
        else 0,
        "no_leak_violations": int((~out["v2_no_leak_valid"].astype(bool)).sum()),
        "rows_by_source_universe": value_counts(out["source_universe"]) if "source_universe" in out else {},
        "probability_ready_by_source_universe": value_counts(prob_ready["source_universe"])
        if len(prob_ready) and "source_universe" in prob_ready
        else {},
        "outputs": {
            "candidates": args.output,
            "summary": args.summary,
        },
        "next_gate": {
            "minimum_non_locked_probability_ready": 500,
            "minimum_validation_rows": 125,
            "allowed_models": [
                "B0_market_only",
                "B1a_intercept_only",
                "B1b_ultra_small",
                "B1c_compact_v2",
            ],
            "forbidden": [
                "GBM",
                "CatBoost",
                "calibration",
                "threshold_tuning",
                "execution_replay",
                "locked_execution_audit",
                "trading_logic",
            ],
        },
    }
    summary_path = Path(args.summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
