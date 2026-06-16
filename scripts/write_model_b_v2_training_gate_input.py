#!/usr/bin/env python3
"""Freeze Model B v2 training inputs before any constrained model fit."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-file", default="data/train_pool/model_b_v2_candidates.csv")
    parser.add_argument("--train-file", default="data/train_pool/train_v2.csv")
    parser.add_argument("--validation-file", default="data/train_pool/validation_v2.csv")
    parser.add_argument("--gate-report", default="reports/model_b_v2_data_gate.json")
    parser.add_argument("--split-report", default="reports/train_validation_split_v2_report.json")
    parser.add_argument("--leakage-report", default="reports/dataset_leakage_checks.json")
    parser.add_argument("--decision-config", default="configs/decision_ts_v1.yaml")
    parser.add_argument("--output", default="reports/model_b_v2_training_gate_input.json")
    args = parser.parse_args()

    files = {
        "candidate_file": Path(args.candidate_file),
        "train_file": Path(args.train_file),
        "validation_file": Path(args.validation_file),
        "gate_report": Path(args.gate_report),
        "decision_config": Path(args.decision_config),
    }
    missing = [str(path) for path in files.values() if not path.exists()]
    if missing:
        raise SystemExit(f"missing required input files: {missing}")

    gate = load_json(Path(args.gate_report))
    split = load_json(Path(args.split_report))
    leakage = load_json(Path(args.leakage_report)) if Path(args.leakage_report).exists() else {}
    payload = {
        "probability_ready_rows": int(gate.get("probability_ready_rows", split.get("candidate_rows", 0))),
        "train_rows": int(split.get("train_rows", 0)),
        "validation_rows": int(split.get("validation_rows", 0)),
        "locked_leakage": int(gate.get("locked_leakage", leakage.get("locked_rows_in_train", 0) + leakage.get("locked_rows_in_validation", 0))),
        "series_leakage": int(gate.get("series_leakage", split.get("series_overlap", 0))),
        "proxy_ts_violations": int(gate.get("proxy_ts_gt_decision_ts_violations", leakage.get("proxy_ts_gt_decision_ts_violations", 0))),
        "last_trade_proxy_rows": int(gate.get("last_trade_proxy_rows", leakage.get("last_trade_proxy_rows", 0))),
        "team_strength_available": int(gate.get("team_strength_available", 0)),
        "candidate_file": args.candidate_file,
        "train_file": args.train_file,
        "validation_file": args.validation_file,
        "gate_report": args.gate_report,
        "decision_config": args.decision_config,
        "sha256": {key: sha256(path) for key, path in files.items()},
    }
    hard_failures = []
    if payload["probability_ready_rows"] < 500:
        hard_failures.append("probability_ready_rows_below_500")
    if payload["validation_rows"] < 125:
        hard_failures.append("validation_rows_below_125")
    for key in ["locked_leakage", "series_leakage", "proxy_ts_violations", "last_trade_proxy_rows"]:
        if payload[key] != 0:
            hard_failures.append(f"{key}_nonzero")
    payload["hard_failures"] = hard_failures
    payload["status"] = "pass" if not hard_failures else "fail"
    if hard_failures:
        raise SystemExit(json.dumps(payload, indent=2, sort_keys=True))

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
