#!/usr/bin/env python3
"""Train/evaluate B0 market-only and B1 compact logistic residual."""

from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize


FEATURES = [
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


def calibration(y: np.ndarray, p: np.ndarray) -> dict[str, float | None]:
    if len(np.unique(y)) < 2 or len(y) < 5:
        return {"intercept": None, "slope": None}
    z = logit(p)
    X = np.column_stack([np.ones(len(z)), z])

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        pred = sigmoid(X @ beta)
        loss = -np.sum(y * np.log(np.clip(pred, 1e-9, 1)) + (1 - y) * np.log(np.clip(1 - pred, 1e-9, 1)))
        grad = X.T @ (pred - y)
        return float(loss), grad

    res = minimize(lambda b: objective(b)[0], np.array([0.0, 1.0]), jac=lambda b: objective(b)[1], method="BFGS")
    if not res.success:
        return {"intercept": None, "slope": None}
    return {"intercept": float(res.x[0]), "slope": float(res.x[1])}


def load_frame(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    for col in FEATURES + ["team_a_win", "p_market_early_mid"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=FEATURES + ["team_a_win", "p_market_early_mid"]).copy()
    return df


def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    return {
        "rows": int(len(y)),
        "log_loss": log_loss(y, p),
        "brier": brier(y, p),
        "mean_predicted_probability": float(np.mean(p)),
        "actual_win_rate": float(np.mean(y)),
        "calibration": calibration(y, p),
    }


def standardize(train: pd.DataFrame, other: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, dict[str, float], dict[str, float]]:
    means = train[FEATURES].mean()
    stds = train[FEATURES].std(ddof=0).replace(0, 1.0).fillna(1.0)
    x_train = ((train[FEATURES] - means) / stds).to_numpy(dtype=float)
    x_other = ((other[FEATURES] - means) / stds).to_numpy(dtype=float)
    x_train = np.column_stack([np.ones(len(x_train)), x_train])
    x_other = np.column_stack([np.ones(len(x_other)), x_other])
    return x_train, x_other, means.to_dict(), stds.to_dict()


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
    if not res.success:
        # BFGS can report precision loss after reaching a useful optimum on small data.
        if not np.isfinite(res.fun):
            raise RuntimeError(f"offset logistic failed: {res.message}")
    return res.x


def predict(df: pd.DataFrame, x: np.ndarray, beta: np.ndarray) -> np.ndarray:
    return sigmoid(logit(df["p_market_early_mid"].to_numpy(dtype=float)) + x @ beta)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", default="data/train_pool/train.csv")
    parser.add_argument("--validation", default="data/train_pool/validation.csv")
    parser.add_argument("--model-output", default="models/model_b/b1_logistic_residual.pkl")
    parser.add_argument("--b0-report", default="models/model_b/b0_market_only_report.json")
    parser.add_argument("--b1-report", default="models/model_b/b1_logistic_residual_report.json")
    parser.add_argument("--alphas", default="0.01,0.1,1.0")
    args = parser.parse_args()

    train = load_frame(Path(args.train))
    val = load_frame(Path(args.validation))
    y_train = train["team_a_win"].to_numpy(dtype=float)
    y_val = val["team_a_win"].to_numpy(dtype=float)
    p_train = train["p_market_early_mid"].to_numpy(dtype=float)
    p_val = val["p_market_early_mid"].to_numpy(dtype=float)
    x_train, x_val, means, stds = standardize(train, val)

    b0 = {
        "train": metrics(y_train, p_train),
        "validation": metrics(y_val, p_val),
    }

    alpha_reports = []
    best = None
    for alpha in [float(x.strip()) for x in args.alphas.split(",") if x.strip()]:
        beta = fit_offset_logistic(x_train, y_train, logit(p_train), alpha)
        pred_train = predict(train, x_train, beta)
        pred_val = predict(val, x_val, beta)
        report = {
            "alpha": alpha,
            "train": metrics(y_train, pred_train),
            "validation": metrics(y_val, pred_val),
            "coefficients": dict(zip(["intercept"] + FEATURES, [float(x) for x in beta])),
        }
        alpha_reports.append(report)
        if best is None or report["validation"]["log_loss"] < best["validation"]["log_loss"]:
            best = report
            best_beta = beta

    assert best is not None
    model = {
        "model_type": "offset_logistic_residual",
        "features": FEATURES,
        "alpha": best["alpha"],
        "beta": best_beta,
        "feature_means": means,
        "feature_stds": stds,
        "anchor": "logit(p_market_early_mid)",
    }
    model_path = Path(args.model_output)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    with model_path.open("wb") as f:
        pickle.dump(model, f)
    Path(args.b0_report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.b0_report).write_text(json.dumps(b0, indent=2, sort_keys=True), encoding="utf-8")
    b1 = {
        "selected_alpha": best["alpha"],
        "selected_by": "validation_log_loss",
        "alpha_sweep": alpha_reports,
        "selected": best,
        "warnings": ["validation_selected_alpha_small_sample"],
    }
    Path(args.b1_report).write_text(json.dumps(b1, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"b0_validation": b0["validation"], "b1_selected": best["validation"], "selected_alpha": best["alpha"]}, indent=2, sort_keys=True))
    print(f"wrote {args.b0_report}")
    print(f"wrote {args.b1_report}")
    print(f"wrote {args.model_output}")


if __name__ == "__main__":
    main()
