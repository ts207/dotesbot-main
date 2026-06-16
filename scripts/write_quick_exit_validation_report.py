#!/usr/bin/env python3
"""Write a concise validation status report for frozen quick-exit candidate."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oos-report", default="reports/quick_exit_stale_book_oos_report.json", type=Path)
    parser.add_argument("--output", default="reports/quick_exit_stale_book_validation_summary.json", type=Path)
    args = parser.parse_args()

    oos = json.loads(args.oos_report.read_text(encoding="utf-8"))
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy": oos.get("strategy"),
        "status": oos.get("status"),
        "pass": oos.get("pass"),
        "reason": oos.get("reason"),
        "signals": oos.get("signals"),
        "fills": oos.get("fills"),
        "exit_liquidity": oos.get("exit_liquidity"),
        "net_pnl": oos.get("net_pnl"),
        "roi": oos.get("roi"),
        "checks": oos.get("checks"),
        "next_action": "collect_fresh_forward_data" if oos.get("status") == "not_run" else "review_validation_result",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")
    print(json.dumps({"status": summary["status"], "pass": summary["pass"], "next_action": summary["next_action"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
