#!/usr/bin/env python3
"""Evaluate Model B validation performance and source robustness."""

from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd


def sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -40, 40)))


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def log_loss(y: np.ndarray, p: np.ndarray) -> float:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def load_frame(path: Path, features: list[str]) -> pd.DataFrame:
    df = pd.read_csv(path)
    for col in features + ["team_a_win", "p_market_early_mid"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=features + ["team_a_win", "p_market_early_mid"]).copy()


def predict(df: pd.DataFrame, model: dict) -> np.ndarray:
    features = model["features"]
    x = df[features].copy()
    for feature in features:
        x[feature] = (x[feature] - model["feature_means"][feature]) / model["feature_stds"][feature]
    x_np = np.column_stack([np.ones(len(x)), x.to_numpy(dtype=float)])
    return sigmoid(logit(df["p_market_early_mid"].to_numpy(dtype=float)) + x_np @ model["beta"])


def metric_block(df: pd.DataFrame, p_b0: np.ndarray, p_b1: np.ndarray) -> dict:
    y = df["team_a_win"].to_numpy(dtype=float)
    return {
        "rows": int(len(df)),
        "b0_log_loss": log_loss(y, p_b0),
        "b0_brier": brier(y, p_b0),
        "b1_log_loss": log_loss(y, p_b1),
        "b1_brier": brier(y, p_b1),
        "log_loss_improvement": log_loss(y, p_b0) - log_loss(y, p_b1),
        "brier_improvement": brier(y, p_b0) - brier(y, p_b1),
        "mean_b0_probability": float(np.mean(p_b0)),
        "mean_b1_probability": float(np.mean(p_b1)),
        "actual_win_rate": float(np.mean(y)),
    }


def by_bucket(df: pd.DataFrame, p_b0: np.ndarray, p_b1: np.ndarray, column: str) -> dict:
    out = {}
    if column not in df.columns:
        return out
    for value, idx in df.groupby(column, dropna=False).groups.items():
        idx_list = list(idx)
        if not idx_list:
            continue
        sub = df.loc[idx_list]
        out[str(value)] = metric_block(sub, p_b0[df.index.get_indexer(idx_list)], p_b1[df.index.get_indexer(idx_list)])
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", default="data/train_pool/train.csv")
    parser.add_argument("--validation", default="data/train_pool/validation.csv")
    parser.add_argument("--model", default="models/model_b/b1_logistic_residual.pkl")
    parser.add_argument("--b0-report", default="models/model_b/b0_market_only_report.json")
    parser.add_argument("--b1-report", default="models/model_b/b1_logistic_residual_report.json")
    parser.add_argument("--output", default="reports/model_b_validation_report.json")
    args = parser.parse_args()

    with Path(args.model).open("rb") as f:
        model = pickle.load(f)
    train = load_frame(Path(args.train), model["features"])
    val = load_frame(Path(args.validation), model["features"]).reset_index(drop=True)
    p_b0 = val["p_market_early_mid"].to_numpy(dtype=float)
    p_b1 = predict(val, model)
    y = val["team_a_win"].to_numpy(dtype=float)
    overall = metric_block(val, p_b0, p_b1)
    coefficients = dict(zip(["intercept"] + model["features"], [float(x) for x in model["beta"]]))

    warnings = []
    if len(val) < 50:
        warnings.append("validation_rows_below_50")
    for column in ["market_probability_source", "market_discovery_source", "source_universe"]:
        if column in val.columns:
            top_share = float(val[column].value_counts(normalize=True, dropna=False).iloc[0])
            if top_share > 0.75:
                warnings.append(f"{column}_bucket_dominates_validation")
    if max(abs(v) for v in coefficients.values()) > 3:
        warnings.append("large_abs_coefficient_gt_3")
    if float(np.mean((p_b1 < 0.02) | (p_b1 > 0.98))) > 0.05:
        warnings.append("b1_saturated_predictions_gt_5pct")
    if float(np.mean((p_b1 < 0.001) | (p_b1 > 0.999))) > 0.0:
        warnings.append("b1_extreme_predictions_present")
    for feature in model["features"]:
        train_mean = float(train[feature].mean())
        train_std = float(train[feature].std(ddof=0)) or 1.0
        val_mean = float(val[feature].mean())
        if abs(val_mean - train_mean) > 2 * train_std:
            warnings.append(f"feature_shift_gt_2std:{feature}")

    bucket_reports = {
        "by_proxy_source": by_bucket(val, p_b0, p_b1, "market_probability_source"),
        "by_discovery_source": by_bucket(val, p_b0, p_b1, "market_discovery_source"),
        "by_source_universe": by_bucket(val, p_b0, p_b1, "source_universe"),
        "by_team_a_is_radiant": by_bucket(val, p_b0, p_b1, "team_a_is_radiant"),
    }
    improved_buckets = 0
    checked_buckets = 0
    for report in bucket_reports.values():
        for block in report.values():
            if block["rows"] >= 5:
                checked_buckets += 1
                if block["log_loss_improvement"] > 0 and block["brier_improvement"] > 0:
                    improved_buckets += 1
    if overall["log_loss_improvement"] > 0 and overall["brier_improvement"] > 0 and improved_buckets <= 1 and checked_buckets > 1:
        warnings.append("b1_improvement_isolated_to_one_or_zero_source_buckets")

    report = {
        "train_rows": int(len(train)),
        "validation_rows": int(len(val)),
        "b0_market_only": {
            "log_loss": overall["b0_log_loss"],
            "brier": overall["b0_brier"],
        },
        "b1_logistic_residual": {
            "log_loss": overall["b1_log_loss"],
            "brier": overall["b1_brier"],
        },
        "delta": {
            "log_loss_improvement": overall["log_loss_improvement"],
            "brier_improvement": overall["brier_improvement"],
        },
        **bucket_reports,
        "coefficients": coefficients,
        "selected_alpha": model["alpha"],
        "warnings": warnings,
        "verdict": "pass" if overall["log_loss_improvement"] > 0 and overall["brier_improvement"] > 0 else "fail",
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
