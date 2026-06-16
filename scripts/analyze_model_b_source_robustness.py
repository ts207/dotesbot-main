#!/usr/bin/env python3
"""Source-stratified diagnostics for the Model B residual.

This script is intentionally diagnostic-only. It does not save a tradable model,
choose thresholds, calibrate, or touch the locked execution audit. Its job is to
answer whether the weak high-regularization residual signal survives when the
validation rows are split by market/proxy source.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from scipy.optimize import minimize


FEATURE_SETS = {
    "intercept_only": [],
    "ultra_small": [
        "team_a_is_radiant",
        "scaling_diff",
        "tempo_diff",
        "fight_diff",
        "tower_diff",
        "fallback_share",
    ],
    "compact_hygiene": [
        "team_a_is_radiant",
        "scaling_diff",
        "tempo_diff",
        "fight_diff",
        "tower_diff",
        "volatility_diff",
        "a_tempo_b_scaling",
        "a_scaling_b_tempo",
        "fallback_share",
    ],
}

SOURCE_COLUMNS = [
    "market_probability_source",
    "source_universe",
    "market_discovery_source",
]

DEFAULT_ALPHAS = [100.0]
MIN_TRAIN_ROWS = 30
MIN_VALIDATION_ROWS = 10


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


def safe_float(value: object) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def load_frame(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    numeric = {"team_a_win", "p_market_early_mid"}
    for features in FEATURE_SETS.values():
        numeric.update(features)
    for col in numeric:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    required = ["team_a_win", "p_market_early_mid"]
    return df.dropna(subset=required).reset_index(drop=True)


def metric_block(y: np.ndarray, p_b0: np.ndarray, p_model: np.ndarray) -> dict:
    b0_log_loss = log_loss(y, p_b0)
    model_log_loss = log_loss(y, p_model)
    b0_brier = brier(y, p_b0)
    model_brier = brier(y, p_model)
    return {
        "rows": int(len(y)),
        "b0_log_loss": b0_log_loss,
        "model_log_loss": model_log_loss,
        "log_loss_improvement": b0_log_loss - model_log_loss,
        "b0_brier": b0_brier,
        "model_brier": model_brier,
        "brier_improvement": b0_brier - model_brier,
        "mean_market_probability": float(np.mean(p_b0)),
        "mean_model_probability": float(np.mean(p_model)),
        "actual_win_rate": float(np.mean(y)),
    }


def standardize(
    train: pd.DataFrame, other: pd.DataFrame, features: list[str]
) -> tuple[np.ndarray, np.ndarray, dict[str, float], dict[str, float]]:
    if not features:
        return np.ones((len(train), 1)), np.ones((len(other), 1)), {}, {}
    means = train[features].mean()
    stds = train[features].std(ddof=0).replace(0, 1.0).fillna(1.0)
    x_train = ((train[features] - means) / stds).to_numpy(dtype=float)
    x_other = ((other[features] - means) / stds).to_numpy(dtype=float)
    return (
        np.column_stack([np.ones(len(x_train)), x_train]),
        np.column_stack([np.ones(len(x_other)), x_other]),
        {k: float(v) for k, v in means.to_dict().items()},
        {k: float(v) for k, v in stds.to_dict().items()},
    )


def fit_offset_logistic(x: np.ndarray, y: np.ndarray, offset: np.ndarray, alpha: float) -> np.ndarray:
    penalty = np.ones(x.shape[1])
    penalty[0] = 0.0

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        z = offset + x @ beta
        p = sigmoid(z)
        nll = -np.sum(
            y * np.log(np.clip(p, 1e-9, 1))
            + (1 - y) * np.log(np.clip(1 - p, 1e-9, 1))
        )
        reg = 0.5 * alpha * np.sum(penalty * beta * beta)
        grad = x.T @ (p - y) + alpha * penalty * beta
        return float(nll + reg), grad

    res = minimize(
        lambda b: objective(b)[0],
        np.zeros(x.shape[1]),
        jac=lambda b: objective(b)[1],
        method="BFGS",
    )
    if not res.success and not np.isfinite(res.fun):
        raise RuntimeError(f"offset logistic failed: {res.message}")
    return res.x


def fit_predict(
    train: pd.DataFrame,
    val: pd.DataFrame,
    features: list[str],
    alpha: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    model_train = train.dropna(subset=features).reset_index(drop=True) if features else train.reset_index(drop=True)
    model_val = val.dropna(subset=features).reset_index(drop=True) if features else val.reset_index(drop=True)
    if len(model_train) != len(train) or len(model_val) != len(val):
        raise ValueError("missing feature rows are not supported inside fit_predict")
    y_train = model_train["team_a_win"].to_numpy(dtype=float)
    p_train = model_train["p_market_early_mid"].to_numpy(dtype=float)
    p_val = model_val["p_market_early_mid"].to_numpy(dtype=float)
    x_train, x_val, _, _ = standardize(model_train, model_val, features)
    beta = fit_offset_logistic(x_train, y_train, logit(p_train), alpha)
    residual = x_val @ beta
    p_model = sigmoid(logit(p_val) + residual)
    return p_model, residual, beta


def source_values(train: pd.DataFrame, val: pd.DataFrame, column: str) -> list[str]:
    values: set[str] = set()
    if column in train.columns:
        values.update(str(x) for x in train[column].fillna("missing").unique())
    if column in val.columns:
        values.update(str(x) for x in val[column].fillna("missing").unique())
    return sorted(values)


def add_row(
    rows: list[dict],
    experiment: str,
    source_column: str,
    source_value: str,
    model_name: str,
    alpha: float,
    train_subset: pd.DataFrame,
    val_subset: pd.DataFrame,
    p_model: np.ndarray,
    residual: np.ndarray,
    notes: str = "",
) -> None:
    y = val_subset["team_a_win"].to_numpy(dtype=float)
    p_b0 = val_subset["p_market_early_mid"].to_numpy(dtype=float)
    metrics = metric_block(y, p_b0, p_model)
    rows.append(
        {
            "experiment": experiment,
            "source_column": source_column,
            "source_value": source_value,
            "model_name": model_name,
            "alpha": alpha,
            "train_rows": int(len(train_subset)),
            "validation_rows": int(len(val_subset)),
            "mean_residual_adjustment": float(np.mean(residual)),
            "std_residual_adjustment": float(np.std(residual)),
            "min_residual_adjustment": float(np.min(residual)),
            "max_residual_adjustment": float(np.max(residual)),
            "p_model_lt_0_05": int(np.sum(p_model < 0.05)),
            "p_model_gt_0_95": int(np.sum(p_model > 0.95)),
            "notes": notes,
            **metrics,
        }
    )


def compact_rows(rows: Iterable[dict]) -> list[dict]:
    return [
        {
            k: (round(v, 8) if isinstance(v, float) else v)
            for k, v in row.items()
        }
        for row in rows
    ]


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "experiment",
        "source_column",
        "source_value",
        "model_name",
        "alpha",
        "train_rows",
        "validation_rows",
        "b0_log_loss",
        "model_log_loss",
        "log_loss_improvement",
        "b0_brier",
        "model_brier",
        "brier_improvement",
        "mean_market_probability",
        "mean_model_probability",
        "actual_win_rate",
        "mean_residual_adjustment",
        "std_residual_adjustment",
        "min_residual_adjustment",
        "max_residual_adjustment",
        "p_model_lt_0_05",
        "p_model_gt_0_95",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({h: row.get(h, "") for h in headers})


def source_counts(df: pd.DataFrame) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for col in SOURCE_COLUMNS:
        if col in df.columns:
            out[col] = {str(k): int(v) for k, v in df[col].fillna("missing").value_counts().to_dict().items()}
    return out


def summarize(rows: list[dict]) -> dict:
    global_rows = [
        r for r in rows
        if r["experiment"] == "global_model_overall"
        and r["model_name"] in {"intercept_only", "ultra_small", "compact_hygiene"}
    ]
    bucket_rows = [r for r in rows if r["experiment"] == "global_model_bucket"]
    within_rows = [r for r in rows if r["experiment"] == "within_source_fit"]
    leaveout_rows = [r for r in rows if r["experiment"] == "leave_source_out_fit"]

    def count_positive(items: list[dict]) -> dict:
        return {
            "rows": len(items),
            "positive_log_loss_improvement": int(sum((safe_float(r.get("log_loss_improvement")) or 0.0) > 0 for r in items)),
            "positive_brier_improvement": int(sum((safe_float(r.get("brier_improvement")) or 0.0) > 0 for r in items)),
        }

    warnings = []
    ultra = next((r for r in global_rows if r["model_name"] == "ultra_small"), None)
    compact = next((r for r in global_rows if r["model_name"] == "compact_hygiene"), None)
    if ultra and (safe_float(ultra.get("log_loss_improvement")) or 0.0) < 0.005:
        warnings.append("ultra_small_global_improvement_tiny")
    if compact and (safe_float(compact.get("log_loss_improvement")) or 0.0) <= 0:
        warnings.append("compact_hygiene_global_fails")
    if bucket_rows:
        positive = [r for r in bucket_rows if (safe_float(r.get("log_loss_improvement")) or 0.0) > 0]
        if len(positive) < max(1, len(bucket_rows) // 3):
            warnings.append("source_bucket_improvement_not_broad")
    ultra_bucket_rows = [
        r for r in bucket_rows
        if r["model_name"] == "ultra_small"
    ]
    for source_column in SOURCE_COLUMNS:
        col_rows = [r for r in ultra_bucket_rows if r["source_column"] == source_column]
        if not col_rows:
            continue
        dominant = max(col_rows, key=lambda r: int(r["validation_rows"]))
        if (safe_float(dominant.get("log_loss_improvement")) or 0.0) <= 0:
            warnings.append(f"ultra_small_dominant_{source_column}_bucket_fails:{dominant['source_value']}")
    if within_rows and not any((safe_float(r.get("log_loss_improvement")) or 0.0) > 0 for r in within_rows):
        warnings.append("within_source_refits_do_not_improve")

    return {
        "global_models": compact_rows(global_rows),
        "bucket_summary": count_positive(bucket_rows),
        "within_source_summary": count_positive(within_rows),
        "leave_source_out_summary": count_positive(leaveout_rows),
        "warnings": warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", default="data/train_pool/train.csv")
    parser.add_argument("--validation", default="data/train_pool/validation.csv")
    parser.add_argument("--output-json", default="reports/model_b_source_robustness.json")
    parser.add_argument("--output-csv", default="reports/model_b_source_robustness.csv")
    parser.add_argument("--alphas", default=",".join(str(x) for x in DEFAULT_ALPHAS))
    args = parser.parse_args()

    train = load_frame(Path(args.train))
    val = load_frame(Path(args.validation))
    alphas = [float(x.strip()) for x in args.alphas.split(",") if x.strip()]
    rows: list[dict] = []

    for model_name, features in FEATURE_SETS.items():
        model_train = train.dropna(subset=features).reset_index(drop=True) if features else train.reset_index(drop=True)
        model_val = val.dropna(subset=features).reset_index(drop=True) if features else val.reset_index(drop=True)
        for alpha in alphas:
            p_model, residual, _ = fit_predict(model_train, model_val, features, alpha)
            add_row(
                rows,
                "global_model_overall",
                "all",
                "all",
                model_name,
                alpha,
                model_train,
                model_val,
                p_model,
                residual,
            )
            for col in SOURCE_COLUMNS:
                if col not in model_val.columns:
                    continue
                for value, idx in model_val.groupby(col, dropna=False).groups.items():
                    positions = model_val.index.get_indexer(list(idx))
                    subset = model_val.loc[list(idx)].reset_index(drop=True)
                    if len(subset) < 5:
                        continue
                    add_row(
                        rows,
                        "global_model_bucket",
                        col,
                        str(value),
                        model_name,
                        alpha,
                        model_train,
                        subset,
                        p_model[positions],
                        residual[positions],
                    )

    # Source-specific fits use the most conservative diagnostic model only.
    source_model = "ultra_small"
    source_features = FEATURE_SETS[source_model]
    source_alpha = max(alphas)
    for col in SOURCE_COLUMNS:
        if col not in train.columns or col not in val.columns:
            continue
        for value in source_values(train, val, col):
            train_mask = train[col].fillna("missing").astype(str) == value
            val_mask = val[col].fillna("missing").astype(str) == value
            train_subset = train.loc[train_mask].dropna(subset=source_features).reset_index(drop=True)
            val_subset = val.loc[val_mask].dropna(subset=source_features).reset_index(drop=True)
            if len(train_subset) >= MIN_TRAIN_ROWS and len(val_subset) >= MIN_VALIDATION_ROWS:
                p_model, residual, _ = fit_predict(train_subset, val_subset, source_features, source_alpha)
                add_row(
                    rows,
                    "within_source_fit",
                    col,
                    value,
                    source_model,
                    source_alpha,
                    train_subset,
                    val_subset,
                    p_model,
                    residual,
                )

            leave_train = train.loc[~train_mask].dropna(subset=source_features).reset_index(drop=True)
            leave_val = val_subset
            if len(leave_train) >= MIN_TRAIN_ROWS and len(leave_val) >= MIN_VALIDATION_ROWS:
                p_model, residual, _ = fit_predict(leave_train, leave_val, source_features, source_alpha)
                add_row(
                    rows,
                    "leave_source_out_fit",
                    col,
                    value,
                    source_model,
                    source_alpha,
                    leave_train,
                    leave_val,
                    p_model,
                    residual,
                    notes=f"trained_without_{col}={value}",
                )

    rows = compact_rows(rows)
    write_csv(Path(args.output_csv), rows)

    report = {
        "train_rows": int(len(train)),
        "validation_rows": int(len(val)),
        "alphas": alphas,
        "feature_sets": FEATURE_SETS,
        "source_counts_train": source_counts(train),
        "source_counts_validation": source_counts(val),
        "summary": summarize(rows),
        "rows": rows,
    }
    Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_json).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"wrote {args.output_json}")
    print(f"wrote {args.output_csv}")


if __name__ == "__main__":
    main()
