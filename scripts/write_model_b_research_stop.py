#!/usr/bin/env python3
"""Write the Model B v1 research-stop report.

This report freezes the current decision: B1 residual variants do not provide
source-robust evidence over the Polymarket early-price anchor, so no model or
execution advancement is allowed from this run.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


FORBIDDEN_NEXT_STEPS = [
    "GBM",
    "CatBoost",
    "hero_categorical_model",
    "calibration",
    "threshold_tuning",
    "execution_replay",
    "locked_audit",
    "execution_simulation",
    "trading_logic",
    "live_logic",
]

ALLOWED_NEXT_STEPS = [
    "collect_more_non_locked_rows",
    "improve_features",
    "rebuild_fresh_validation_split",
    "rerun_b0_market_only",
    "rerun_b1a_intercept_only",
    "rerun_b1b_ultra_small",
    "rerun_b1c_compact",
]


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def find_global_model(source_report: dict[str, Any], model_name: str) -> dict[str, Any]:
    for row in source_report.get("summary", {}).get("global_models", []):
        if row.get("model_name") == model_name:
            return row
    return {}


def best_residual(source_report: dict[str, Any]) -> dict[str, Any]:
    candidates = [
        row
        for row in source_report.get("summary", {}).get("global_models", [])
        if row.get("model_name") not in {"intercept_only"}
    ]
    if not candidates:
        return {}
    return max(candidates, key=lambda row: float(row.get("log_loss_improvement", float("-inf"))))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", default="data/train_pool/summary.json")
    parser.add_argument("--b0-report", default="models/model_b/b0_market_only_report.json")
    parser.add_argument("--b1-report", default="models/model_b/b1_logistic_residual_report.json")
    parser.add_argument("--source-report", default="reports/model_b_source_robustness.json")
    parser.add_argument("--leakage-report", default="reports/dataset_leakage_checks.json")
    parser.add_argument("--output", default="reports/model_b_v1_research_stop.json")
    args = parser.parse_args()

    summary = load_json(Path(args.summary))
    b0_report = load_json(Path(args.b0_report))
    b1_report = load_json(Path(args.b1_report))
    source_report = load_json(Path(args.source_report))
    leakage_report = load_json(Path(args.leakage_report))

    best = best_residual(source_report)
    ultra_small = find_global_model(source_report, "ultra_small")
    compact = find_global_model(source_report, "compact_hygiene")

    validation_b0 = b0_report.get("validation", {})
    selected_b1 = b1_report.get("selected", {}).get("validation", {})

    report = {
        "status": "stop_no_trade",
        "model_version": "model_b_v1",
        "reason": "residual_not_source_robust",
        "model_target": "p_fair = sigmoid(logit(p_market_early_mid) + residual_adjustment)",
        "benchmark": "B0 = p_market_early_mid",
        "decision": {
            "advance_model_class": False,
            "calibrate": False,
            "tune_thresholds": False,
            "run_locked_audit": False,
            "simulate_execution": False,
            "trade": False,
        },
        "dataset": {
            "non_locked_probability_ready": summary.get("non_locked_probability_ready"),
            "train_rows": summary.get("train_rows"),
            "validation_rows": summary.get("validation_rows"),
            "locked_execution_audit_expected": summary.get("locked_execution_audit_expected"),
            "locked_execution_audit_materialized": summary.get("locked_execution_audit_materialized"),
            "locked_missing_or_unresolved": summary.get("locked_missing_or_unresolved"),
        },
        "leakage_checks": {
            "status": leakage_report.get("status"),
            "locked_rows_in_train": leakage_report.get("locked_rows_in_train"),
            "locked_rows_in_validation": leakage_report.get("locked_rows_in_validation"),
            "train_validation_series_overlap": leakage_report.get("train_validation_series_overlap"),
            "proxy_ts_gt_decision_ts_violations": leakage_report.get("proxy_ts_gt_decision_ts_violations"),
            "last_trade_proxy_rows": leakage_report.get("last_trade_proxy_rows"),
        },
        "b0_validation": {
            "log_loss": validation_b0.get("log_loss"),
            "brier": validation_b0.get("brier"),
            "rows": validation_b0.get("rows"),
            "mean_predicted_probability": validation_b0.get("mean_predicted_probability"),
            "actual_win_rate": validation_b0.get("actual_win_rate"),
        },
        "formal_b1_compact_validation": {
            "log_loss": selected_b1.get("log_loss"),
            "brier": selected_b1.get("brier"),
            "selected_alpha": b1_report.get("selected_alpha"),
            "verdict": "fail",
        },
        "best_residual_variant": {
            "name": best.get("model_name"),
            "alpha": best.get("alpha"),
            "log_loss_improvement": best.get("log_loss_improvement"),
            "brier_improvement": best.get("brier_improvement"),
            "model_log_loss": best.get("model_log_loss"),
            "model_brier": best.get("model_brier"),
            "interpretation": "diagnostic_only_not_source_robust",
        },
        "diagnostic_variants": {
            "ultra_small": {
                "log_loss_improvement": ultra_small.get("log_loss_improvement"),
                "brier_improvement": ultra_small.get("brier_improvement"),
                "model_log_loss": ultra_small.get("model_log_loss"),
                "model_brier": ultra_small.get("model_brier"),
            },
            "compact_hygiene": {
                "log_loss_improvement": compact.get("log_loss_improvement"),
                "brier_improvement": compact.get("brier_improvement"),
                "model_log_loss": compact.get("model_log_loss"),
                "model_brier": compact.get("model_brier"),
            },
        },
        "robustness_failures": [
            "dominant price_history_proxy bucket worsens",
            "dominant polymarket_public_search bucket worsens",
            "source-specific refits 0/2 improve",
            "compact hygiene fails",
        ],
        "source_robustness_warnings": source_report.get("summary", {}).get("warnings", []),
        "forbidden_next_steps": FORBIDDEN_NEXT_STEPS,
        "allowed_next_steps": ALLOWED_NEXT_STEPS,
        "next_research_order": [
            "freeze_current_result_as_model_b_v1_failed",
            "keep_b0_as_benchmark",
            "expand_non_locked_dataset_toward_250_to_500_plus_rows",
            "add_stronger_non_market_features",
            "rebuild_fresh_temporal_series_split",
            "rerun_only_constrained_residual_models",
        ],
        "advancement_gate": [
            "residual_model_beats_b0_global_log_loss",
            "residual_model_beats_b0_global_brier",
            "residual_model_beats_b0_in_dominant_price_history_proxy_bucket",
            "residual_model_beats_b0_in_dominant_polymarket_public_search_bucket",
            "source_specific_refits_are_not_negative",
        ],
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "reason": report["reason"],
        "non_locked_probability_ready": report["dataset"]["non_locked_probability_ready"],
        "b0_log_loss": report["b0_validation"]["log_loss"],
        "best_residual_variant": report["best_residual_variant"],
        "output": str(output_path),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
