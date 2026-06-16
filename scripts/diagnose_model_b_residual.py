#!/usr/bin/env python3
"""Diagnose Model B residual mechanics without advancing model class."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize


FEATURE_SETS = {
    "b1a_intercept_only": [],
    "b1b_ultra_small": [
        "team_a_is_radiant",
        "scaling_diff",
        "tempo_diff",
        "fight_diff",
        "tower_diff",
        "fallback_share",
    ],
    "b1c_compact_hygiene": [
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

ALPHAS = [0.1, 1.0, 10.0, 100.0]


PREDICTION_HEADERS = [
    "split",
    "market_id",
    "model_name",
    "alpha",
    "team_a_win",
    "p_market_early_mid",
    "residual_adjustment",
    "p_model",
    "market_probability_source",
    "market_discovery_source",
    "source_universe",
]


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


def load_frame(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    numeric = set(["team_a_win", "p_market_early_mid"]).union(*(set(v) for v in FEATURE_SETS.values()))
    for col in numeric:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["team_a_win", "p_market_early_mid"]).copy()


def standardize(train: pd.DataFrame, other: pd.DataFrame, features: list[str]) -> tuple[np.ndarray, np.ndarray, dict, dict]:
    if not features:
        return np.ones((len(train), 1)), np.ones((len(other), 1)), {}, {}
    means = train[features].mean()
    stds = train[features].std(ddof=0).replace(0, 1.0).fillna(1.0)
    x_train = ((train[features] - means) / stds).to_numpy(dtype=float)
    x_other = ((other[features] - means) / stds).to_numpy(dtype=float)
    return (
        np.column_stack([np.ones(len(x_train)), x_train]),
        np.column_stack([np.ones(len(x_other)), x_other]),
        means.to_dict(),
        stds.to_dict(),
    )


def fit_offset_logistic(x: np.ndarray, y: np.ndarray, offset: np.ndarray, alpha: float) -> np.ndarray:
    penalty = np.ones(x.shape[1])
    penalty[0] = 0.0

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        z = offset + x @ beta
        p = sigmoid(z)
        nll = -np.sum(y * np.log(np.clip(p, 1e-9, 1)) + (1 - y) * np.log(np.clip(1 - p, 1e-9, 1)))
        reg = 0.5 * alpha * np.sum(penalty * beta * beta)
        grad = x.T @ (p - y) + alpha * penalty * beta
        return float(nll + reg), grad

    res = minimize(lambda b: objective(b)[0], np.zeros(x.shape[1]), jac=lambda b: objective(b)[1], method="BFGS")
    if not res.success and not np.isfinite(res.fun):
        raise RuntimeError(f"offset logistic failed: {res.message}")
    return res.x


def metric_block(y: np.ndarray, p: np.ndarray) -> dict:
    return {
        "rows": int(len(y)),
        "log_loss": log_loss(y, p),
        "brier": brier(y, p),
        "mean_predicted_probability": float(np.mean(p)),
        "actual_win_rate": float(np.mean(y)),
    }


def residual_stats(residual: np.ndarray, p: np.ndarray) -> dict:
    return {
        "mean_residual_adjustment": float(np.mean(residual)),
        "std_residual_adjustment": float(np.std(residual)),
        "min_residual_adjustment": float(np.min(residual)),
        "max_residual_adjustment": float(np.max(residual)),
        "p1_residual_adjustment": float(np.percentile(residual, 1)),
        "p99_residual_adjustment": float(np.percentile(residual, 99)),
        "min_p_model": float(np.min(p)),
        "max_p_model": float(np.max(p)),
        "count_p_model_lt_0_05": int(np.sum(p < 0.05)),
        "count_p_model_gt_0_95": int(np.sum(p > 0.95)),
        "count_p_model_lt_0_001": int(np.sum(p < 0.001)),
        "count_p_model_gt_0_999": int(np.sum(p > 0.999)),
    }


def by_bucket(df: pd.DataFrame, p_b0: np.ndarray, p_model: np.ndarray, column: str) -> dict:
    out = {}
    if column not in df.columns:
        return out
    for value, idx in df.groupby(column, dropna=False).groups.items():
        positions = df.index.get_indexer(list(idx))
        sub = df.loc[list(idx)]
        y = sub["team_a_win"].to_numpy(dtype=float)
        out[str(value)] = {
            "rows": int(len(sub)),
            "b0_log_loss": log_loss(y, p_b0[positions]),
            "b0_brier": brier(y, p_b0[positions]),
            "model_log_loss": log_loss(y, p_model[positions]),
            "model_brier": brier(y, p_model[positions]),
            "log_loss_improvement": log_loss(y, p_b0[positions]) - log_loss(y, p_model[positions]),
            "brier_improvement": brier(y, p_b0[positions]) - brier(y, p_model[positions]),
        }
    return out


def feature_report(train: pd.DataFrame, val: pd.DataFrame) -> dict:
    all_features = sorted(set().union(*(set(v) for v in FEATURE_SETS.values())))
    out = {}
    for feature in all_features:
        train_std = float(train[feature].std(ddof=0)) if feature in train else 0.0
        val_mean = float(val[feature].mean()) if feature in val else None
        train_mean = float(train[feature].mean()) if feature in train else None
        out[feature] = {
            "train_mean": train_mean,
            "validation_mean": val_mean,
            "train_std": train_std,
            "validation_std": float(val[feature].std(ddof=0)) if feature in val else None,
            "zero_train_variance": bool(train_std == 0.0),
            "validation_shift_gt_2_train_std": bool(train_mean is not None and val_mean is not None and abs(val_mean - train_mean) > 2 * (train_std + 1e-9)),
        }
    return out


def prediction_rows(split: str, df: pd.DataFrame, model_name: str, alpha: float, residual: np.ndarray, p_model: np.ndarray) -> list[dict[str, str]]:
    rows = []
    for i, row in df.reset_index(drop=True).iterrows():
        rows.append(
            {
                "split": split,
                "market_id": str(row.get("market_id", "")),
                "model_name": model_name,
                "alpha": f"{alpha:.6g}",
                "team_a_win": str(row.get("team_a_win", "")),
                "p_market_early_mid": str(row.get("p_market_early_mid", "")),
                "residual_adjustment": f"{float(residual[i]):.8f}",
                "p_model": f"{float(p_model[i]):.8f}",
                "market_probability_source": str(row.get("market_probability_source", "")),
                "market_discovery_source": str(row.get("market_discovery_source", "")),
                "source_universe": str(row.get("source_universe", "")),
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", default="data/train_pool/train.csv")
    parser.add_argument("--validation", default="data/train_pool/validation.csv")
    parser.add_argument("--output", default="reports/model_b_residual_diagnostics.json")
    parser.add_argument("--predictions-output", default="reports/model_b_residual_predictions.csv")
    args = parser.parse_args()

    train = load_frame(Path(args.train)).reset_index(drop=True)
    val = load_frame(Path(args.validation)).reset_index(drop=True)
    y_train = train["team_a_win"].to_numpy(dtype=float)
    y_val = val["team_a_win"].to_numpy(dtype=float)
    p0_train = train["p_market_early_mid"].to_numpy(dtype=float)
    p0_val = val["p_market_early_mid"].to_numpy(dtype=float)
    offset_repro_train = sigmoid(logit(p0_train) + np.zeros_like(p0_train))
    offset_repro_val = sigmoid(logit(p0_val) + np.zeros_like(p0_val))
    offset_check = {
        "train_max_abs_diff": float(np.max(np.abs(offset_repro_train - p0_train))),
        "validation_max_abs_diff": float(np.max(np.abs(offset_repro_val - p0_val))),
        "passes": bool(np.max(np.abs(offset_repro_train - p0_train)) < 1e-12 and np.max(np.abs(offset_repro_val - p0_val)) < 1e-12),
    }

    diagnostics = {
        "train_rows": int(len(train)),
        "validation_rows": int(len(val)),
        "offset_reproduction_check": offset_check,
        "b0_market_only": {
            "train": metric_block(y_train, p0_train),
            "validation": metric_block(y_val, p0_val),
        },
        "feature_variance_and_shift": feature_report(train, val),
        "models": {},
        "warnings": [],
    }
    pred_rows = []
    if not offset_check["passes"]:
        diagnostics["warnings"].append("offset_reproduction_failed")
    if len(val) < 50:
        diagnostics["warnings"].append("validation_rows_below_50")

    for model_name, features in FEATURE_SETS.items():
        model_reports = {}
        x_train, x_val, _means, _stds = standardize(train, val, features)
        for alpha in ALPHAS:
            beta = fit_offset_logistic(x_train, y_train, logit(p0_train), alpha)
            residual_train = x_train @ beta
            residual_val = x_val @ beta
            p_train = sigmoid(logit(p0_train) + residual_train)
            p_val = sigmoid(logit(p0_val) + residual_val)
            key = f"alpha_{alpha:g}"
            model_reports[key] = {
                "alpha": alpha,
                "features": features,
                "coefficients": dict(zip(["intercept"] + features, [float(x) for x in beta])),
                "train": metric_block(y_train, p_train),
                "validation": metric_block(y_val, p_val),
                "validation_delta_vs_b0": {
                    "log_loss_improvement": log_loss(y_val, p0_val) - log_loss(y_val, p_val),
                    "brier_improvement": brier(y_val, p0_val) - brier(y_val, p_val),
                },
                "train_residual_stats": residual_stats(residual_train, p_train),
                "validation_residual_stats": residual_stats(residual_val, p_val),
                "validation_by_proxy_source": by_bucket(val, p0_val, p_val, "market_probability_source"),
                "validation_by_discovery_source": by_bucket(val, p0_val, p_val, "market_discovery_source"),
                "validation_by_source_universe": by_bucket(val, p0_val, p_val, "source_universe"),
            }
            pred_rows.extend(prediction_rows("validation", val, model_name, alpha, residual_val, p_val))
        diagnostics["models"][model_name] = model_reports

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(diagnostics, indent=2, sort_keys=True), encoding="utf-8")
    with Path(args.predictions_output).open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=PREDICTION_HEADERS)
        writer.writeheader()
        writer.writerows(pred_rows)
    print(json.dumps({
        "offset_reproduction_check": offset_check,
        "b0_validation": diagnostics["b0_market_only"]["validation"],
        "model_keys": list(diagnostics["models"].keys()),
        "warnings": diagnostics["warnings"],
    }, indent=2, sort_keys=True))
    print(f"wrote {args.output}")
    print(f"wrote {args.predictions_output}")


if __name__ == "__main__":
    main()
