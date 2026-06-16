#!/usr/bin/env python3
"""Freeze the current Value bot candidate for guarded validation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except Exception:
    pass

from scripts.backtest_value_engine import _params  # noqa: E402


VALUE_ENV_KEYS = [
    "VALUE_MIN_EDGE",
    "VALUE_MIN_FAIR",
    "VALUE_MIN_NW_LEAD",
    "VALUE_MIN_GAME_TIME",
    "VALUE_MAX_PRICE",
    "VALUE_MIN_PRICE",
    "VALUE_MAX_EDGE",
    "VALUE_MAX_GAME_TIME",
    "VALUE_MAX_BOOK_AGE_MS",
    "VALUE_FLIP_LEAD",
    "VALUE_FLIP_ASK_FLOOR",
    "VALUE_TRADE_USD",
    "VALUE_LIVE_MAX_USD",
    "ENABLE_VALUE_TRADING",
    "VALUE_ENGINE_ENABLED",
]


def sha256_json(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def compare_params(current: dict, reference: dict) -> dict:
    out = {}
    for key in sorted(set(current) | set(reference)):
        cur = current.get(key)
        ref = reference.get(key)
        out[key] = {
            "current": cur,
            "backtest_reference": ref,
            "matches": cur == ref,
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backtest-report", default="reports/bot_performance_backtest_2026_06_07.json", type=Path)
    parser.add_argument("--output", default="reports/value_bot_strategy_freeze.json", type=Path)
    args = parser.parse_args()

    backtest = load_json(args.backtest_report)
    value_ref = backtest.get("value_settlement_backtest", {})
    reference_params = value_ref.get("params", {})
    current_params = _params()
    relevant_env = {key: os.getenv(key) for key in VALUE_ENV_KEYS}
    env_hash = sha256_json({"value_env": relevant_env, "current_params": current_params})
    engine_hash = sha256_file(REPO_ROOT / "value_engine.py")
    backtest_hash = sha256_file(args.backtest_report)
    param_comparison = compare_params(current_params, reference_params)
    params_match = all(item["matches"] for item in param_comparison.values())

    freeze = {
        "strategy": "value_bot",
        "status": "primary_candidate_requires_guarded_validation",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "runtime_env_hash": env_hash,
        "value_engine_hash": engine_hash,
        "backtest_report_hash": backtest_hash,
        "runtime_params": current_params,
        "backtest_reference_params": reference_params,
        "runtime_matches_backtest_reference": params_match,
        "param_comparison": param_comparison,
        "entry_rules": {
            "top_live_only": True,
            "map_winner_or_game3_proxy_only": True,
            "lead_time_price_gates": True,
            "edge_cap": True,
            "anti_flip_price_floor": True,
            "book_age_checks": True,
            "orientation_flip_guard": True,
        },
        "backtest_reference": {
            "no_confirmation": value_ref.get("no_confirmation", {}),
            "with_confirmation": value_ref.get("with_confirmation", {}),
            "coverage": value_ref.get("coverage", {}),
            "source_report": str(args.backtest_report),
        },
        "trading_status": "not_live_until_raw_replay_and_shadow_pass",
        "validation_requirements": {
            "causality_violations": 0,
            "manual_windows_excluded": True,
            "entry_at_actual_ask": True,
            "orientation_flips": 0,
            "concentration_acceptable": True,
            "forward_shadow_min_signals_preferred": 50,
            "forward_shadow_min_high_quality_signals": 20,
        },
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(freeze, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")
    print(json.dumps({"runtime_matches_backtest_reference": params_match, "status": freeze["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
