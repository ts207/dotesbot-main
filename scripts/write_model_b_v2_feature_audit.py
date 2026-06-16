#!/usr/bin/env python3
"""Write a Model B v2 feature audit report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def numeric_summary(series: pd.Series) -> dict[str, Any]:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return {
            "count": 0,
            "mean": None,
            "std": None,
            "min": None,
            "p25": None,
            "median": None,
            "p75": None,
            "max": None,
        }
    return {
        "count": int(len(values)),
        "mean": float(values.mean()),
        "std": float(values.std(ddof=0)),
        "min": float(values.min()),
        "p25": float(values.quantile(0.25)),
        "median": float(values.median()),
        "p75": float(values.quantile(0.75)),
        "max": float(values.max()),
    }


def counts(series: pd.Series) -> dict[str, int]:
    return {str(k): int(v) for k, v in series.fillna("missing").value_counts().to_dict().items()}


def parse_ts(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    parsed = pd.to_datetime(series, utc=True, errors="coerce")
    mask = numeric.notna()
    if mask.any():
        parsed.loc[mask] = pd.to_datetime(numeric[mask], unit="s", utc=True, errors="coerce")
    return parsed


def feature_snapshot_age_days(df: pd.DataFrame) -> pd.Series:
    if "feature_snapshot_ts" not in df.columns or "start_ts" not in df.columns:
        return pd.Series([pd.NA] * len(df))
    start = parse_ts(df["start_ts"])
    snapshot = pd.to_datetime(df["feature_snapshot_ts"], utc=True, errors="coerce")
    return (start - snapshot).dt.total_seconds() / 86400.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", default="data/train_pool/model_b_v2_candidates.csv")
    parser.add_argument("--v2-summary", default="data/train_pool/model_b_v2_summary.json")
    parser.add_argument("--team-strength-report", default="reports/team_strength_features_report.json")
    parser.add_argument("--roster-report", default="reports/roster_continuity_features_report.json")
    parser.add_argument("--synergy-report", default="reports/draft_synergy_counter_features_report.json")
    parser.add_argument("--output", default="reports/model_b_v2_feature_audit.json")
    args = parser.parse_args()

    df = pd.read_csv(args.candidates)
    v2_summary = load_json(Path(args.v2_summary))
    team_report = load_json(Path(args.team_strength_report))
    roster_report = load_json(Path(args.roster_report))
    synergy_report = load_json(Path(args.synergy_report))

    ready = df[df["analysis_ready_probability"].astype(bool)].copy()
    ready["feature_snapshot_age_days"] = feature_snapshot_age_days(ready)

    audit = {
        "status": "ok",
        "model_b_v2_status": "feature_data_build_only",
        "training_allowed": False,
        "training_blocker": "non_locked_probability_ready_below_500_or_validation_rows_below_125",
        "probability_ready_rows": int(len(ready)),
        "team_strength_available": int(ready["v2_team_strength_feature_available"].astype(bool).sum()),
        "team_strength_confidence_ge_0_5": int(ready["v2_team_strength_confidence_ge_0_5"].astype(bool).sum()),
        "team_strength_confidence_ge_0_8": int(ready["v2_team_strength_confidence_ge_0_8"].astype(bool).sum()),
        "no_leak_violations": int((~ready["v2_no_leak_valid"].astype(bool)).sum()),
        "blocked_features": {
            "roster_continuity": roster_report.get(
                "reason", "missing stable player/account IDs"
            ),
            "draft_synergy_counter": synergy_report.get(
                "reason", "insufficient rolling draft/outcome corpus"
            ),
        },
        "distribution_audits": {
            "team_strength_confidence": numeric_summary(ready["team_strength_feature_confidence"]),
            "team_strength_diff": numeric_summary(ready["team_strength_diff"]),
            "recent_form_diff": numeric_summary(ready["team_recent_form_diff"]),
            "feature_snapshot_age_days": numeric_summary(ready["feature_snapshot_age_days"]),
            "source_universe": counts(ready["source_universe"]) if "source_universe" in ready else {},
            "market_probability_source": counts(ready["market_probability_source"])
            if "market_probability_source" in ready
            else {},
        },
        "coverage": {
            "v2_summary": {
                "probability_ready_rows": v2_summary.get("probability_ready_rows"),
                "probability_ready_team_strength_available_rows": v2_summary.get(
                    "probability_ready_team_strength_available_rows"
                ),
                "probability_ready_team_strength_confidence_ge_0_5_rows": v2_summary.get(
                    "probability_ready_team_strength_confidence_ge_0_5_rows"
                ),
                "probability_ready_team_strength_confidence_ge_0_8_rows": v2_summary.get(
                    "probability_ready_team_strength_confidence_ge_0_8_rows"
                ),
            },
            "team_strength_report": {
                "rows": team_report.get("rows"),
                "team_strength_diff_non_null": team_report.get("team_strength_diff_non_null"),
                "team_recent_form_diff_non_null": team_report.get("team_recent_form_diff_non_null"),
                "mean_team_strength_feature_confidence": team_report.get(
                    "mean_team_strength_feature_confidence"
                ),
                "no_leak_violations": team_report.get("no_leak_violations"),
            },
        },
        "next_data_gate": {
            "minimum_non_locked_probability_ready": 500,
            "minimum_validation_rows": 125,
            "additional_probability_ready_rows_needed_from_current": max(0, 500 - int(len(ready))),
        },
        "forbidden_until_next_pass": [
            "B0_B1_v2_training",
            "GBM",
            "CatBoost",
            "calibration",
            "threshold_tuning",
            "execution_replay",
            "locked_execution_audit",
            "trading_logic",
        ],
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "status": audit["status"],
        "probability_ready_rows": audit["probability_ready_rows"],
        "team_strength_available": audit["team_strength_available"],
        "team_strength_confidence_ge_0_5": audit["team_strength_confidence_ge_0_5"],
        "team_strength_confidence_ge_0_8": audit["team_strength_confidence_ge_0_8"],
        "no_leak_violations": audit["no_leak_violations"],
        "additional_probability_ready_rows_needed": audit["next_data_gate"][
            "additional_probability_ready_rows_needed_from_current"
        ],
        "output": str(output),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
