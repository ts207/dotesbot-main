#!/usr/bin/env python3
"""Freeze the quick_exit_stale_book_v1 candidate selected from development replay."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def find_candidate(report: dict) -> dict:
    for row in report.get("by_mode_horizon", []):
        if row.get("event_family_mode") == "all" and int(row.get("exit_horizon_sec")) == 30:
            return row
    raise SystemExit("missing all-events 30s candidate in quick-exit report")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default="reports/quick_exit_stale_book_report.json", type=Path)
    parser.add_argument("--output", default="reports/quick_exit_stale_book_v1_freeze.json", type=Path)
    args = parser.parse_args()

    report = load_json(args.report)
    candidate = find_candidate(report)
    freeze = {
        "strategy": "quick_exit_stale_book_v1",
        "status": "candidate_requires_fresh_validation",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "entry_source": "existing_signals",
        "fill_model": "conservative_trade_through",
        "entry_price": "ask",
        "exit_price": "bid",
        "exit_horizon_seconds": 30,
        "event_filter": "all_events",
        "model_b_residual_used": False,
        "gbm_used": False,
        "calibration_used": False,
        "threshold_tuning_allowed": False,
        "locked_audit_allowed_for_tuning": False,
        "development_result": {
            "net_pnl": candidate.get("net_pnl"),
            "roi": candidate.get("roi"),
            "exit_liquidity": candidate.get("exit_liquidity_rate"),
            "signals": candidate.get("signals"),
            "fills": candidate.get("fills"),
            "exits": candidate.get("exits"),
            "fill_rate": candidate.get("fill_rate"),
            "top_1_trade_pnl_share": candidate.get("top_1_trade_pnl_share"),
            "top_3_trade_pnl_share": candidate.get("top_3_trade_pnl_share"),
            "top_5_trade_pnl_share": candidate.get("top_5_trade_pnl_share"),
            "source_report": str(args.report),
        },
        "validation_requirements": {
            "minimum_signals": 100,
            "minimum_conservative_fills": 40,
            "require_positive_net_pnl": True,
            "require_positive_roi": True,
            "minimum_exit_liquidity": 0.80,
            "require_not_top5_dominated": True,
            "require_latency_source_delay_stability": True,
        },
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(freeze, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")
    print(json.dumps(freeze["development_result"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
