#!/usr/bin/env python3
"""Write the Model B v2 data-gate report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def int_value(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-summary", default="data/train_pool/summary.json")
    parser.add_argument("--v2-summary", default="data/train_pool/model_b_v2_summary.json")
    parser.add_argument("--feature-audit", default="reports/model_b_v2_feature_audit.json")
    parser.add_argument("--leakage-report", default="reports/dataset_leakage_checks.json")
    parser.add_argument("--split-report", default="reports/train_validation_split_report.json")
    parser.add_argument("--output", default="reports/model_b_v2_data_gate.json")
    args = parser.parse_args()

    train_summary = load_json(Path(args.train_summary))
    v2_summary = load_json(Path(args.v2_summary))
    feature_audit = load_json(Path(args.feature_audit))
    leakage = load_json(Path(args.leakage_report))
    split_report = load_json(Path(args.split_report))

    probability_ready_rows = int_value(train_summary.get("non_locked_probability_ready"))
    validation_rows = int_value(train_summary.get("validation_rows"), default=-1)
    if validation_rows < 0:
        validation_rows = int_value(split_report.get("validation_rows"))
    additional_rows_needed = max(0, 500 - probability_ready_rows)

    locked_leakage = (
        int_value(leakage.get("locked_rows_in_train"))
        + int_value(leakage.get("locked_rows_in_validation"))
    )
    series_leakage = int_value(leakage.get("train_validation_series_overlap"))
    proxy_ts_violations = int_value(leakage.get("proxy_ts_gt_decision_ts_violations"))
    last_trade_proxy_rows = int_value(leakage.get("last_trade_proxy_rows"))
    feature_no_leak_violations = int_value(feature_audit.get("no_leak_violations"))
    v2_no_leak_violations = int_value(v2_summary.get("no_leak_violations"))
    no_leak_violations = feature_no_leak_violations + v2_no_leak_violations

    checks = {
        "non_locked_probability_ready_ge_500": probability_ready_rows >= 500,
        "validation_rows_ge_125": validation_rows >= 125,
        "locked_leakage_eq_0": locked_leakage == 0,
        "series_leakage_eq_0": series_leakage == 0,
        "proxy_ts_gt_decision_ts_violations_eq_0": proxy_ts_violations == 0,
        "trait_team_strength_no_leak_violations_eq_0": no_leak_violations == 0,
        "last_trade_proxy_rows_eq_0": last_trade_proxy_rows == 0,
    }
    gate_status = "pass" if all(checks.values()) else "fail"

    report = {
        "gate_status": gate_status,
        "probability_ready_rows": probability_ready_rows,
        "validation_rows": validation_rows,
        "additional_rows_needed": additional_rows_needed,
        "team_strength_available": int_value(feature_audit.get("team_strength_available")),
        "team_strength_confidence_ge_0_5": int_value(feature_audit.get("team_strength_confidence_ge_0_5")),
        "team_strength_confidence_ge_0_8": int_value(feature_audit.get("team_strength_confidence_ge_0_8")),
        "no_leak_violations": no_leak_violations,
        "locked_leakage": locked_leakage,
        "series_leakage": series_leakage,
        "proxy_ts_gt_decision_ts_violations": proxy_ts_violations,
        "last_trade_proxy_rows": last_trade_proxy_rows,
        "checks": checks,
        "allowed_next_models_if_pass": [
            "B0_market_only",
            "B1a_intercept_only",
            "B1b_ultra_small_v2",
            "B1c_compact_v2",
        ],
        "forbidden_even_if_pass": [
            "GBM",
            "CatBoost",
            "calibration",
            "threshold_tuning",
            "execution_replay",
            "locked_execution_audit",
            "trading_logic",
        ],
        "inputs": {
            "train_summary": args.train_summary,
            "v2_summary": args.v2_summary,
            "feature_audit": args.feature_audit,
            "leakage_report": args.leakage_report,
            "split_report": args.split_report,
        },
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "gate_status": gate_status,
        "probability_ready_rows": probability_ready_rows,
        "validation_rows": validation_rows,
        "additional_rows_needed": additional_rows_needed,
        "team_strength_available": report["team_strength_available"],
        "no_leak_violations": no_leak_violations,
        "output": str(output),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
