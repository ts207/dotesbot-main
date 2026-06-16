#!/usr/bin/env python3
"""Audit compact Model B feature readiness."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
import pandas as pd


V1_FEATURES = [
    "team_a_is_radiant",
    "scaling_diff",
    "tempo_diff",
    "lane_diff",
    "fight_diff",
    "tower_diff",
    "volatility_diff",
    "a_tempo_b_scaling",
    "a_scaling_b_tempo",
    "role_inference_confidence",
    "fallback_share",
    "timestamp_confidence",
]

V2_FEATURES = [
    "team_a_is_radiant",
    "team_strength_diff",
    "team_recent_form_diff",
    "team_strength_feature_confidence",
    "fallback_share",
    "scaling_diff",
    "tempo_diff",
    "fight_diff",
    "tower_diff",
    "volatility_diff",
    "a_tempo_b_scaling",
    "a_scaling_b_tempo",
    "trait_coverage",
]


HEADERS = [
    "feature",
    "train_missing",
    "validation_missing",
    "train_mean",
    "validation_mean",
    "train_std",
    "validation_std",
    "train_min",
    "train_max",
    "validation_min",
    "validation_max",
    "train_target_corr",
    "validation_target_corr",
    "warning",
]


def load_rows(path: Path, features: list[str]) -> pd.DataFrame:
    df = pd.read_csv(path)
    for col in features + ["team_a_win", "p_market_early_mid"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def corr(x: pd.Series, y: pd.Series) -> str:
    valid = x.notna() & y.notna()
    if valid.sum() < 3 or x[valid].std(ddof=0) == 0 or y[valid].std(ddof=0) == 0:
        return ""
    return f"{float(np.corrcoef(x[valid], y[valid])[0, 1]):.6f}"


def fmt(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value):.6f}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["v1", "v2"], default="v1")
    parser.add_argument("--train", default="data/train_pool/train.csv")
    parser.add_argument("--validation", default="data/train_pool/validation.csv")
    parser.add_argument("--output", default=None)
    parser.add_argument("--summary-output", default=None)
    args = parser.parse_args()
    features = V2_FEATURES if args.mode == "v2" else V1_FEATURES
    if args.mode == "v2":
        if args.train == "data/train_pool/train.csv":
            args.train = "data/train_pool/train_v2.csv"
        if args.validation == "data/train_pool/validation.csv":
            args.validation = "data/train_pool/validation_v2.csv"
        args.output = args.output or "reports/model_b_v2_feature_audit.csv"
        args.summary_output = args.summary_output or "reports/model_b_v2_feature_audit_summary.json"
    else:
        args.output = args.output or "reports/model_b_feature_audit.csv"
        args.summary_output = args.summary_output or "reports/model_b_feature_audit_summary.json"

    train = load_rows(Path(args.train), features)
    val = load_rows(Path(args.validation), features)
    rows = []
    warnings = []
    for feature in features:
        if feature not in train.columns or feature not in val.columns:
            warning = "missing_feature_column"
            warnings.append(f"{feature}: missing column")
            series_train = pd.Series(dtype=float)
            series_val = pd.Series(dtype=float)
        else:
            series_train = train[feature]
            series_val = val[feature]
            warning_parts = []
            if series_train.isna().any() or series_val.isna().any():
                warning_parts.append("missing_values")
            if series_train.std(ddof=0) == 0:
                warning_parts.append("zero_train_variance")
            if abs(series_train.mean() - series_val.mean()) > 2 * (series_train.std(ddof=0) + 1e-9):
                warning_parts.append("large_train_val_mean_shift")
            warning = ",".join(warning_parts)
            if warning:
                warnings.append(f"{feature}: {warning}")
        rows.append(
            {
                "feature": feature,
                "train_missing": str(int(series_train.isna().sum())) if len(series_train) else "",
                "validation_missing": str(int(series_val.isna().sum())) if len(series_val) else "",
                "train_mean": fmt(series_train.mean() if len(series_train) else None),
                "validation_mean": fmt(series_val.mean() if len(series_val) else None),
                "train_std": fmt(series_train.std(ddof=0) if len(series_train) else None),
                "validation_std": fmt(series_val.std(ddof=0) if len(series_val) else None),
                "train_min": fmt(series_train.min() if len(series_train) else None),
                "train_max": fmt(series_train.max() if len(series_train) else None),
                "validation_min": fmt(series_val.min() if len(series_val) else None),
                "validation_max": fmt(series_val.max() if len(series_val) else None),
                "train_target_corr": corr(series_train, train["team_a_win"]) if "team_a_win" in train else "",
                "validation_target_corr": corr(series_val, val["team_a_win"]) if "team_a_win" in val else "",
                "warning": warning,
            }
        )

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.output).open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "mode": args.mode,
        "train_rows": int(len(train)),
        "validation_rows": int(len(val)),
        "features": features,
        "warnings": warnings,
    }
    Path(args.summary_output).write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"wrote {args.output}")
    print(f"wrote {args.summary_output}")


if __name__ == "__main__":
    main()
