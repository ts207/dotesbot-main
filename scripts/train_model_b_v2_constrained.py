#!/usr/bin/env python3
"""Run the constrained Model B v2 residual gate.

This is diagnostic-only. It does not calibrate, choose thresholds, replay
execution, open the locked audit, or produce a deployable trading model.
"""

from __future__ import annotations

import argparse
import csv
import json
import pickle
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize


FEATURE_SETS = {
    "b1a_intercept_only": [],
    "b1b_ultra_small": [
        "team_a_is_radiant",
        "team_strength_diff",
        "team_recent_form_diff",
        "team_strength_feature_confidence",
        "fallback_share",
    ],
    "b1c_compact": [
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
    ],
}

ALPHAS = {
    "b1a_intercept_only": [0.0],
    "b1b_ultra_small": [1.0, 10.0, 100.0],
    "b1c_compact": [1.0, 10.0, 100.0],
}

REQUIRED_V2_COLUMNS = sorted(
    {
        "market_id",
        "game_id",
        "team_a_win",
        "p_market_early_mid",
        "market_probability_source",
        "market_discovery_source",
        "source_universe",
        "team_a_is_radiant",
        "team_strength_diff",
        "team_recent_form_diff",
        "team_strength_feature_confidence",
        "v2_no_leak_valid",
    }
)

BUCKET_COLUMNS = [
    "market_probability_source",
    "market_discovery_source",
    "source_universe",
    "team_strength_confidence_bucket",
    "team_a_is_radiant",
]

PREDICTION_HEADERS = [
    "market_id",
    "game_id",
    "model_name",
    "alpha",
    "selected_model",
    "team_a_win",
    "p_market_early_mid",
    "p_model",
    "residual_adjustment",
    "market_probability_source",
    "market_discovery_source",
    "source_universe",
    "team_strength_confidence_bucket",
    "team_a_is_radiant",
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
    if len(y) < 5 or len(np.unique(y)) < 2:
        return {"intercept": None, "slope": None}
    z = logit(p)
    x = np.column_stack([np.ones(len(z)), z])

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        pred = sigmoid(x @ beta)
        loss = -np.sum(
            y * np.log(np.clip(pred, 1e-9, 1))
            + (1 - y) * np.log(np.clip(1 - pred, 1e-9, 1))
        )
        grad = x.T @ (pred - y)
        return float(loss), grad

    res = minimize(lambda b: objective(b)[0], np.array([0.0, 1.0]), jac=lambda b: objective(b)[1], method="BFGS")
    if not res.success and not np.isfinite(res.fun):
        return {"intercept": None, "slope": None}
    return {"intercept": float(res.x[0]), "slope": float(res.x[1])}


def load_frame(path: Path, label: str) -> pd.DataFrame:
    if path.name in {"train.csv", "validation.csv"}:
        # This is allowed only if the caller explicitly points generic names at
        # v2-shaped files. The required-column check below is the real guard.
        pass
    df = pd.read_csv(path)
    missing = [col for col in REQUIRED_V2_COLUMNS if col not in df.columns]
    if missing:
        raise SystemExit(f"{label} is not v2-shaped; missing columns: {missing}")
    for col in sorted({"team_a_win", "p_market_early_mid"}.union(*[set(v) for v in FEATURE_SETS.values()])):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    def truthy(value: object) -> bool:
        return str(value).strip().lower() in {"1", "true", "yes"}

    if "v2_no_leak_valid" in df.columns and not df["v2_no_leak_valid"].map(truthy).all():
        raise SystemExit(f"{label} has v2 no-leak violations")
    if "market_probability_source" in df.columns and (df["market_probability_source"] == "last_trade_proxy").any():
        raise SystemExit(f"{label} contains last_trade_proxy rows")
    if "is_locked_execution_audit" in df.columns and df["is_locked_execution_audit"].map(truthy).any():
        raise SystemExit(f"{label} contains locked execution audit rows")
    df["team_strength_confidence_bucket"] = pd.cut(
        pd.to_numeric(df["team_strength_feature_confidence"], errors="coerce").fillna(0),
        bins=[-0.001, 0.5, 0.8, 1.001],
        labels=["lt_0_5", "0_5_to_0_8", "gte_0_8"],
    ).astype(str)
    return df.dropna(subset=["team_a_win", "p_market_early_mid"]).reset_index(drop=True)


def metric_block(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    return {
        "rows": int(len(y)),
        "log_loss": log_loss(y, p),
        "brier": brier(y, p),
        "mean_predicted_probability": float(np.mean(p)),
        "actual_win_rate": float(np.mean(y)),
        "calibration": calibration(y, p),
    }


def residual_stats(residual: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    return {
        "residual_adjustment_mean": float(np.mean(residual)),
        "residual_adjustment_std": float(np.std(residual)),
        "residual_adjustment_min": float(np.min(residual)),
        "residual_adjustment_max": float(np.max(residual)),
        "residual_adjustment_p01": float(np.percentile(residual, 1)),
        "residual_adjustment_p99": float(np.percentile(residual, 99)),
        "p_pred_min": float(np.min(p)),
        "p_pred_max": float(np.max(p)),
        "count_p_pred_below_0_05": int(np.sum(p < 0.05)),
        "count_p_pred_above_0_95": int(np.sum(p > 0.95)),
    }


def feature_hygiene(train: pd.DataFrame, val: pd.DataFrame, features: list[str]) -> tuple[list[str], list[dict[str, str]]]:
    kept: list[str] = []
    excluded: list[dict[str, str]] = []
    for feature in features:
        if feature not in train.columns or feature not in val.columns:
            excluded.append({"feature": feature, "reason": "missing_column"})
            continue
        train_series = pd.to_numeric(train[feature], errors="coerce")
        val_series = pd.to_numeric(val[feature], errors="coerce")
        train_missing = float(train_series.isna().mean())
        val_missing = float(val_series.isna().mean())
        train_std = float(train_series.std(ddof=0)) if train_series.notna().any() else 0.0
        train_mean = float(train_series.mean()) if train_series.notna().any() else 0.0
        val_mean = float(val_series.mean()) if val_series.notna().any() else 0.0
        if train_missing > 0.05 or val_missing > 0.05:
            excluded.append({"feature": feature, "reason": "high_missingness"})
        elif train_std == 0.0:
            excluded.append({"feature": feature, "reason": "zero_train_variance"})
        elif abs(train_mean - val_mean) > 2 * (train_std + 1e-9):
            excluded.append({"feature": feature, "reason": "severe_shift"})
        else:
            kept.append(feature)
    return kept, excluded


def standardize(train: pd.DataFrame, val: pd.DataFrame, features: list[str]) -> tuple[np.ndarray, np.ndarray, dict, dict]:
    if not features:
        return np.ones((len(train), 1)), np.ones((len(val), 1)), {}, {}
    means = train[features].mean()
    stds = train[features].std(ddof=0).replace(0, 1.0).fillna(1.0)
    train_x = train[features].fillna(means)
    val_x = val[features].fillna(means)
    x_train = ((train_x - means) / stds).to_numpy(dtype=float)
    x_val = ((val_x - means) / stds).to_numpy(dtype=float)
    return (
        np.column_stack([np.ones(len(x_train)), x_train]),
        np.column_stack([np.ones(len(x_val)), x_val]),
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

    res = minimize(lambda b: objective(b)[0], np.zeros(x.shape[1]), jac=lambda b: objective(b)[1], method="BFGS")
    if not res.success and not np.isfinite(res.fun):
        raise RuntimeError(f"offset logistic failed: {res.message}")
    return res.x


def bucket_metrics(df: pd.DataFrame, p0: np.ndarray, p_model: np.ndarray, column: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if column not in df.columns:
        return out
    for value, idx in df.groupby(column, dropna=False).groups.items():
        pos = df.index.get_indexer(list(idx))
        y = df.loc[list(idx), "team_a_win"].to_numpy(dtype=float)
        out[str(value)] = {
            "rows": int(len(pos)),
            "b0_log_loss": log_loss(y, p0[pos]),
            "model_log_loss": log_loss(y, p_model[pos]),
            "log_loss_improvement": log_loss(y, p0[pos]) - log_loss(y, p_model[pos]),
            "b0_brier": brier(y, p0[pos]),
            "model_brier": brier(y, p_model[pos]),
            "brier_improvement": brier(y, p0[pos]) - brier(y, p_model[pos]),
            "mean_market_probability": float(np.mean(p0[pos])),
            "mean_model_probability": float(np.mean(p_model[pos])),
            "actual_win_rate": float(np.mean(y)),
        }
    return out


def dominant_bucket_pass(by_bucket: dict[str, dict[str, Any]]) -> bool:
    populated = [v for v in by_bucket.values() if int(v["rows"]) >= 5]
    if not populated:
        return True
    dominant = max(populated, key=lambda x: int(x["rows"]))
    return dominant["log_loss_improvement"] > 0 and dominant["brier_improvement"] > 0


def model_passes(report: dict[str, Any], b0_val: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    val = report["validation"]
    if not (b0_val["log_loss"] - val["log_loss"] > 0):
        reasons.append("validation_log_loss_not_improved")
    if not (b0_val["brier"] - val["brier"] > 0):
        reasons.append("validation_brier_not_improved")
    for key in ["by_market_probability_source", "by_market_discovery_source", "by_source_universe"]:
        if not dominant_bucket_pass(report.get(key, {})):
            reasons.append(f"dominant_{key}_bucket_not_improved")
    stats = report["validation_residual_stats"]
    if stats["count_p_pred_below_0_05"] + stats["count_p_pred_above_0_95"] > max(1, int(0.05 * val["rows"])):
        reasons.append("prediction_saturation")
    if max(abs(stats["residual_adjustment_p01"]), abs(stats["residual_adjustment_p99"])) > 2.5:
        reasons.append("residual_adjustments_not_modest")
    if max(abs(v) for v in report["coefficients"].values()) > 5:
        reasons.append("coefficient_abs_gt_5")
    return not reasons, reasons


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", default="data/train_pool/train_v2.csv")
    parser.add_argument("--validation", default="data/train_pool/validation_v2.csv")
    parser.add_argument("--models-dir", default="models/model_b_v2")
    parser.add_argument("--validation-report", default="reports/model_b_v2_validation_report.json")
    parser.add_argument("--training-gate-report", default="reports/model_b_v2_training_gate_report.json")
    parser.add_argument("--predictions-output", default="reports/model_b_v2_predictions_validation.csv")
    args = parser.parse_args()

    train = load_frame(Path(args.train), "train")
    val = load_frame(Path(args.validation), "validation")
    if train["market_id"].isin(val["market_id"]).any():
        raise SystemExit("market overlap between train and validation")
    if train["game_id"].isin(val["game_id"]).any():
        raise SystemExit("game overlap between train and validation")
    if train["series_id"].isin(val["series_id"]).any():
        raise SystemExit("series overlap between train and validation")

    y_train = train["team_a_win"].to_numpy(dtype=float)
    y_val = val["team_a_win"].to_numpy(dtype=float)
    p0_train = train["p_market_early_mid"].to_numpy(dtype=float)
    p0_val = val["p_market_early_mid"].to_numpy(dtype=float)
    b0 = {
        "model_name": "b0_market_only",
        "train": metric_block(y_train, p0_train),
        "validation": metric_block(y_val, p0_val),
        "diagnostic_only": True,
        "not_for_trading": True,
    }
    models_dir = Path(args.models_dir)
    write_json(models_dir / "b0_market_only_report.json", b0)

    validation_report: dict[str, Any] = {
        "train_rows": int(len(train)),
        "validation_rows": int(len(val)),
        "b0_market_only": b0,
        "excluded_features": {},
        "residual_models": {},
        "pass_fail": {},
        "forbidden_next_steps": [
            "GBM",
            "CatBoost",
            "calibration",
            "threshold_tuning",
            "execution_replay",
            "locked_execution_audit",
            "trading_logic",
        ],
    }
    prediction_rows: list[dict[str, Any]] = []
    for model_name, requested_features in FEATURE_SETS.items():
        kept, excluded = feature_hygiene(train, val, requested_features)
        validation_report["excluded_features"][model_name] = excluded
        alpha_reports = []
        selected = None
        selected_payload = None
        for alpha in ALPHAS[model_name]:
            x_train, x_val, means, stds = standardize(train, val, kept)
            beta = fit_offset_logistic(x_train, y_train, logit(p0_train), alpha)
            residual_train = x_train @ beta
            residual_val = x_val @ beta
            p_train = sigmoid(logit(p0_train) + residual_train)
            p_val = sigmoid(logit(p0_val) + residual_val)
            report = {
                "model_name": model_name,
                "alpha": alpha,
                "requested_features": requested_features,
                "features": kept,
                "excluded_features": excluded,
                "coefficients": dict(zip(["intercept"] + kept, [float(x) for x in beta])),
                "train": metric_block(y_train, p_train),
                "validation": metric_block(y_val, p_val),
                "validation_delta_vs_b0": {
                    "log_loss_improvement": b0["validation"]["log_loss"] - log_loss(y_val, p_val),
                    "brier_improvement": b0["validation"]["brier"] - brier(y_val, p_val),
                },
                "train_residual_stats": residual_stats(residual_train, p_train),
                "validation_residual_stats": residual_stats(residual_val, p_val),
                "by_market_probability_source": bucket_metrics(val, p0_val, p_val, "market_probability_source"),
                "by_market_discovery_source": bucket_metrics(val, p0_val, p_val, "market_discovery_source"),
                "by_source_universe": bucket_metrics(val, p0_val, p_val, "source_universe"),
                "by_team_strength_confidence_bucket": bucket_metrics(val, p0_val, p_val, "team_strength_confidence_bucket"),
                "by_team_a_is_radiant": bucket_metrics(val, p0_val, p_val, "team_a_is_radiant"),
            }
            passed, fail_reasons = model_passes(report, b0["validation"])
            report["passes_gate"] = passed
            report["fail_reasons"] = fail_reasons
            alpha_reports.append(report)
            if selected is None or report["validation"]["log_loss"] < selected["validation"]["log_loss"]:
                selected = report
                selected_payload = {
                    "diagnostic_only": True,
                    "not_for_trading": True,
                    "model_name": model_name,
                    "alpha": alpha,
                    "features": kept,
                    "excluded_features": excluded,
                    "feature_means": means,
                    "feature_stds": stds,
                    "beta": beta,
                    "anchor": "logit(p_market_early_mid)",
                }
            for i, row in val.reset_index(drop=True).iterrows():
                prediction_rows.append(
                    {
                        "market_id": row.get("market_id", ""),
                        "game_id": row.get("game_id", ""),
                        "model_name": model_name,
                        "alpha": alpha,
                        "selected_model": False,
                        "team_a_win": row.get("team_a_win", ""),
                        "p_market_early_mid": row.get("p_market_early_mid", ""),
                        "p_model": f"{float(p_val[i]):.8f}",
                        "residual_adjustment": f"{float(residual_val[i]):.8f}",
                        "market_probability_source": row.get("market_probability_source", ""),
                        "market_discovery_source": row.get("market_discovery_source", ""),
                        "source_universe": row.get("source_universe", ""),
                        "team_strength_confidence_bucket": row.get("team_strength_confidence_bucket", ""),
                        "team_a_is_radiant": row.get("team_a_is_radiant", ""),
                    }
                )
        assert selected is not None and selected_payload is not None
        for row in prediction_rows:
            if row["model_name"] == model_name and float(row["alpha"]) == float(selected["alpha"]):
                row["selected_model"] = True
        report_name = {
            "b1a_intercept_only": "b1a_intercept_only_report.json",
            "b1b_ultra_small": "b1b_ultra_small_report.json",
            "b1c_compact": "b1c_compact_report.json",
        }[model_name]
        pkl_name = report_name.replace("_report.json", ".pkl")
        model_report = {
            "diagnostic_only": True,
            "not_for_trading": True,
            "selected_by": "validation_log_loss",
            "selected": selected,
            "alpha_sweep": alpha_reports,
        }
        write_json(models_dir / report_name, model_report)
        with (models_dir / pkl_name).open("wb") as f:
            pickle.dump(selected_payload, f)
        validation_report["residual_models"][model_name] = model_report
        validation_report["pass_fail"][model_name] = {
            "passes_gate": bool(selected["passes_gate"]),
            "fail_reasons": selected["fail_reasons"],
            "selected_alpha": selected["alpha"],
        }

    any_pass = any(v["passes_gate"] for v in validation_report["pass_fail"].values())
    validation_report["verdict"] = "pass_constrained_residual" if any_pass else "fail_current_best_b0_market_only"
    write_json(Path(args.validation_report), validation_report)
    write_json(Path(args.training_gate_report), {
        "train_rows": int(len(train)),
        "validation_rows": int(len(val)),
        "b0_validation": b0["validation"],
        "pass_fail": validation_report["pass_fail"],
        "verdict": validation_report["verdict"],
    })
    Path(args.predictions_output).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.predictions_output).open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=PREDICTION_HEADERS)
        writer.writeheader()
        writer.writerows(prediction_rows)
    print(json.dumps({
        "train_rows": len(train),
        "validation_rows": len(val),
        "b0_validation": b0["validation"],
        "pass_fail": validation_report["pass_fail"],
        "verdict": validation_report["verdict"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
