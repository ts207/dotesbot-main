#!/usr/bin/env python3
"""Write corrected Model B v2 failure diagnosis after source crosstab audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", default="reports/model_b_v2_training_gate_report.json")
    parser.add_argument("--crosstab", default="reports/model_b_v2_source_crosstab.json")
    parser.add_argument("--robustness", default="reports/model_b_v2_source_robustness.json")
    parser.add_argument("--output", default="reports/model_b_v2_corrected_diagnosis.json")
    args = parser.parse_args()
    gate = load_json(Path(args.gate))
    crosstab = load_json(Path(args.crosstab))
    robustness = load_json(Path(args.robustness))
    payload = {
        "status": "stop_no_trade",
        "current_best_model": "B0_market_only",
        "model_b_v2_verdict": gate.get("verdict"),
        "corrected_reason": crosstab.get("summary", {}).get(
            "mechanism_assessment",
            "residual_not_source_robust_mechanism_uncertain",
        ),
        "important_correction": (
            "Do not add probability-source counts to discovery/source-universe counts. "
            "market_probability_source and source_universe are separate axes and can overlap."
        ),
        "b0_validation": gate.get("b0_validation"),
        "residual_pass_fail": gate.get("pass_fail"),
        "crosstab_summary": crosstab.get("summary"),
        "robustness_warnings": robustness.get("warnings", []),
        "forbidden_next_steps": [
            "GBM",
            "CatBoost",
            "calibration",
            "threshold_tuning",
            "execution_replay",
            "locked_execution_audit",
            "trading_logic",
        ],
        "next_valid_work": [
            "use source crosstab when explaining failures",
            "improve feature/data quality",
            "consider source-aware validation design before any future residual gate",
        ],
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
