#!/usr/bin/env python3
"""Summarize source robustness for constrained Model B v2 residuals."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


BUCKET_KEYS = [
    "by_market_probability_source",
    "by_market_discovery_source",
    "by_source_universe",
    "by_team_strength_confidence_bucket",
    "by_team_a_is_radiant",
]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def dominant_bucket(blocks: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]] | None:
    eligible = [(name, block) for name, block in blocks.items() if int(block.get("rows", 0)) >= 5]
    if not eligible:
        return None
    return max(eligible, key=lambda item: int(item[1].get("rows", 0)))


def bucket_summary(blocks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for name, block in blocks.items():
        if int(block.get("rows", 0)) < 5:
            continue
        rows.append(
            {
                "bucket": name,
                "rows": int(block.get("rows", 0)),
                "log_loss_improvement": float(block.get("log_loss_improvement", 0.0)),
                "brier_improvement": float(block.get("brier_improvement", 0.0)),
                "improves_both": block.get("log_loss_improvement", 0.0) > 0
                and block.get("brier_improvement", 0.0) > 0,
            }
        )
    dom = dominant_bucket(blocks)
    return {
        "checked_buckets": rows,
        "dominant_bucket": None if dom is None else dom[0],
        "dominant_bucket_rows": 0 if dom is None else int(dom[1].get("rows", 0)),
        "dominant_bucket_improves": True
        if dom is None
        else dom[1].get("log_loss_improvement", 0.0) > 0 and dom[1].get("brier_improvement", 0.0) > 0,
        "positive_buckets": sum(1 for row in rows if row["improves_both"]),
        "total_buckets": len(rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-report", default="reports/model_b_v2_validation_report.json")
    parser.add_argument("--output", default="reports/model_b_v2_source_robustness.json")
    args = parser.parse_args()

    report = load_json(Path(args.validation_report))
    out: dict[str, Any] = {
        "source": args.validation_report,
        "verdict": report.get("verdict"),
        "models": {},
        "warnings": [],
    }
    for model_name, model_report in report.get("residual_models", {}).items():
        selected = model_report.get("selected", {})
        model_out = {
            "selected_alpha": selected.get("alpha"),
            "passes_gate": selected.get("passes_gate"),
            "fail_reasons": selected.get("fail_reasons", []),
            "validation_delta_vs_b0": selected.get("validation_delta_vs_b0", {}),
            "bucket_checks": {},
        }
        for key in BUCKET_KEYS:
            model_out["bucket_checks"][key] = bucket_summary(selected.get(key, {}))
        for required_key, required_bucket in [
            ("by_market_probability_source", "price_history_proxy"),
            ("by_source_universe", "polymarket_public_search"),
        ]:
            block = selected.get(required_key, {}).get(required_bucket)
            if block and not (block.get("log_loss_improvement", 0.0) > 0 and block.get("brier_improvement", 0.0) > 0):
                out["warnings"].append(f"{model_name}:{required_bucket}_bucket_fails")
        out["models"][model_name] = model_out
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(out, indent=2, sort_keys=True))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
