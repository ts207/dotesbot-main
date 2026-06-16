#!/usr/bin/env python3
"""Cross-tab source axes for Model B v2 diagnostics."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


MODELS = ["b1a_intercept_only", "b1b_ultra_small", "b1c_compact"]
GROUPS = {
    "market_probability_source": ["market_probability_source"],
    "market_discovery_source": ["market_discovery_source"],
    "source_universe": ["source_universe"],
    "probability_x_discovery": ["market_probability_source", "market_discovery_source"],
    "probability_x_source_universe": ["market_probability_source", "source_universe"],
}
CSV_HEADERS = [
    "split",
    "group",
    "market_probability_source",
    "market_discovery_source",
    "source_universe",
    "row_count",
    "actual_win_rate",
    "mean_p_market_early_mid",
    "b0_log_loss",
    "b0_brier",
    "b1a_log_loss",
    "b1a_brier",
    "b1a_log_loss_delta_vs_b0",
    "b1a_brier_delta_vs_b0",
    "b1b_log_loss",
    "b1b_brier",
    "b1b_log_loss_delta_vs_b0",
    "b1b_brier_delta_vs_b0",
    "b1c_log_loss",
    "b1c_brier",
    "b1c_log_loss_delta_vs_b0",
    "b1c_brier_delta_vs_b0",
]


def log_loss(y: np.ndarray, p: np.ndarray) -> float:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def selected_predictions(predictions: pd.DataFrame) -> pd.DataFrame:
    pred = predictions[predictions["selected_model"].astype(str).str.lower().eq("true")].copy()
    keep = ["market_id"]
    for model_name in MODELS:
        sub = pred[pred["model_name"] == model_name][["market_id", "p_model"]].copy()
        sub = sub.rename(columns={"p_model": f"{model_name}_p_model"})
        keep.append(f"{model_name}_p_model")
        if "out" not in locals():
            out = sub
        else:
            out = out.merge(sub, on="market_id", how="outer")
    return out[keep]


def attach_predictions(df: pd.DataFrame, pred_wide: pd.DataFrame | None) -> pd.DataFrame:
    out = df.copy()
    for col in ["team_a_win", "p_market_early_mid"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if pred_wide is not None:
        out = out.merge(pred_wide, on="market_id", how="left")
    for model_name in MODELS:
        col = f"{model_name}_p_model"
        if col not in out.columns:
            out[col] = np.nan
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def metric_row(split: str, group_name: str, keys: list[str], key_values: tuple[Any, ...], df: pd.DataFrame) -> dict[str, Any]:
    y = df["team_a_win"].to_numpy(dtype=float)
    p0 = df["p_market_early_mid"].to_numpy(dtype=float)
    row: dict[str, Any] = {
        "split": split,
        "group": group_name,
        "market_probability_source": "",
        "market_discovery_source": "",
        "source_universe": "",
        "row_count": int(len(df)),
        "actual_win_rate": float(np.mean(y)),
        "mean_p_market_early_mid": float(np.mean(p0)),
        "b0_log_loss": log_loss(y, p0),
        "b0_brier": brier(y, p0),
    }
    for key, value in zip(keys, key_values):
        row[key] = str(value)
    for short, model_name in [("b1a", "b1a_intercept_only"), ("b1b", "b1b_ultra_small"), ("b1c", "b1c_compact")]:
        p_col = f"{model_name}_p_model"
        valid = df[p_col].notna()
        if not valid.all():
            row[f"{short}_log_loss"] = ""
            row[f"{short}_brier"] = ""
            row[f"{short}_log_loss_delta_vs_b0"] = ""
            row[f"{short}_brier_delta_vs_b0"] = ""
            continue
        p_model = df[p_col].to_numpy(dtype=float)
        model_ll = log_loss(y, p_model)
        model_brier = brier(y, p_model)
        row[f"{short}_log_loss"] = model_ll
        row[f"{short}_brier"] = model_brier
        row[f"{short}_log_loss_delta_vs_b0"] = row["b0_log_loss"] - model_ll
        row[f"{short}_brier_delta_vs_b0"] = row["b0_brier"] - model_brier
    return row


def build_rows(split: str, df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group_name, keys in GROUPS.items():
        grouped = df.groupby(keys, dropna=False)
        for key_values, sub in grouped:
            if not isinstance(key_values, tuple):
                key_values = (key_values,)
            rows.append(metric_row(split, group_name, keys, key_values, sub))
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "mechanism_assessment": "residual_not_source_robust_mechanism_uncertain",
        "validation_interaction_failures": [],
        "notes": [
            "market_probability_source and discovery/source_universe are separate axes",
            "cross-tab rows show overlap rather than additive source counts",
        ],
    }
    validation_rows = [r for r in rows if r["split"] == "validation"]
    for group in ["probability_x_discovery", "probability_x_source_universe"]:
        group_rows = [r for r in validation_rows if r["group"] == group and r["row_count"] >= 5]
        for model in ["b1a", "b1b", "b1c"]:
            failed = [
                r for r in group_rows
                if isinstance(r.get(f"{model}_log_loss_delta_vs_b0"), float)
                and (r[f"{model}_log_loss_delta_vs_b0"] <= 0 or r[f"{model}_brier_delta_vs_b0"] <= 0)
            ]
            if failed:
                summary["validation_interaction_failures"].append(
                    {
                        "group": group,
                        "model": model,
                        "failed_bucket_count": len(failed),
                        "failed_rows_total": int(sum(r["row_count"] for r in failed)),
                    }
                )
    if summary["validation_interaction_failures"]:
        summary["mechanism_assessment"] = "residual_not_source_robust_cross_tab_confirmed"
    return summary


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow({h: row.get(h, "") for h in CSV_HEADERS})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", default="data/train_pool/train_v2.csv")
    parser.add_argument("--validation", default="data/train_pool/validation_v2.csv")
    parser.add_argument("--predictions", default="reports/model_b_v2_predictions_validation.csv")
    parser.add_argument("--output-json", default="reports/model_b_v2_source_crosstab.json")
    parser.add_argument("--output-csv", default="reports/model_b_v2_source_crosstab.csv")
    args = parser.parse_args()

    train = attach_predictions(pd.read_csv(args.train), None)
    predictions = selected_predictions(pd.read_csv(args.predictions))
    validation = attach_predictions(pd.read_csv(args.validation), predictions)
    rows = build_rows("train", train) + build_rows("validation", validation)
    payload = {
        "inputs": {
            "train": args.train,
            "validation": args.validation,
            "predictions": args.predictions,
        },
        "summary": summarize(rows),
        "rows": rows,
    }
    Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_json).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(Path(args.output_csv), rows)
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    print(f"wrote {args.output_json}")
    print(f"wrote {args.output_csv}")


if __name__ == "__main__":
    main()
