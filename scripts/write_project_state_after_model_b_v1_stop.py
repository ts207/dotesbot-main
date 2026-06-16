#!/usr/bin/env python3
"""Write the project state after the Model B v1 research stop."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stop-report", default="reports/model_b_v1_research_stop.json")
    parser.add_argument("--output", default="reports/project_state_after_model_b_v1_stop.json")
    args = parser.parse_args()

    stop_report = load_json(Path(args.stop_report))
    dataset = stop_report.get("dataset", {})
    best_residual = stop_report.get("best_residual_variant", {})

    state = {
        "model_b_v1": "stopped",
        "trading_status": "do_not_trade",
        "locked_execution_audit_status": "not_run",
        "current_best_model": "B0_market_only",
        "next_phase": "feature_and_data_improvement",
        "minimum_next_data_target": 500,
        "minimum_next_validation_rows": 125,
        "source_of_truth": str(Path(args.stop_report)),
        "stop_reason": stop_report.get("reason", "residual_not_source_robust"),
        "latest_dataset": {
            "non_locked_probability_ready": dataset.get("non_locked_probability_ready"),
            "train_rows": dataset.get("train_rows"),
            "validation_rows": dataset.get("validation_rows"),
        },
        "best_residual_diagnostic": {
            "name": best_residual.get("name"),
            "alpha": best_residual.get("alpha"),
            "log_loss_improvement": best_residual.get("log_loss_improvement"),
            "brier_improvement": best_residual.get("brier_improvement"),
            "status": best_residual.get("interpretation", "diagnostic_only_not_source_robust"),
        },
        "forbidden_until_next_pass": [
            "GBM",
            "CatBoost",
            "hero_categorical_model",
            "calibration",
            "threshold_tuning",
            "execution_replay",
            "locked_execution_audit",
            "execution_simulation",
            "trading_logic",
            "live_logic",
        ],
        "allowed_next_phase_work": [
            "collect_more_non_locked_rows",
            "improve_team_strength_priors",
            "improve_recent_form_features",
            "improve_roster_continuity_features",
            "improve_tournament_tier_features",
            "improve_patch_age_and_trait_age_features",
            "improve_hero_pair_synergy_features",
            "improve_hero_counter_features",
            "improve_draft_archetype_features",
            "improve_radiant_dire_patch_bias_features",
            "rebuild_fresh_temporal_series_split",
            "rerun_constrained_residual_models_only",
        ],
        "next_modeling_gate": {
            "required_non_locked_probability_ready": 500,
            "required_validation_rows": 125,
            "required_models": [
                "B0_market_only",
                "B1a_intercept_only",
                "B1b_ultra_small",
                "B1c_compact",
            ],
            "required_pass_conditions": [
                "beats_B0_global_log_loss",
                "beats_B0_global_brier",
                "beats_B0_in_dominant_price_history_proxy_bucket",
                "beats_B0_in_dominant_polymarket_public_search_bucket",
                "source_specific_refits_not_negative",
            ],
        },
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "model_b_v1": state["model_b_v1"],
        "trading_status": state["trading_status"],
        "current_best_model": state["current_best_model"],
        "next_phase": state["next_phase"],
        "minimum_next_data_target": state["minimum_next_data_target"],
        "output": str(output),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
