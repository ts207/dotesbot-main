#!/usr/bin/env python3
"""Validate frozen quick_exit_stale_book_v1 on explicit fresh/OOS windows."""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from unified_storage.event_store import parse_ns


HEADERS = [
    "strategy_name",
    "event_family_mode",
    "exit_horizon_sec",
    "signal_id",
    "decision_ts",
    "decision_utc",
    "match_id",
    "market_name",
    "event_type",
    "token_id",
    "entry_fillable",
    "exit_liquidity_available",
    "filled_size_usd",
    "entry_price",
    "exit_bid",
    "net_pnl",
    "roi",
    "source_delay_sec",
    "latency_book_age_at_signal_ms",
    "snapshot_age_sec",
    "book_age_sec",
]


def fnum(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def is_true(value: Any) -> bool:
    return str(value).strip().casefold() == "true"


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow({h: row.get(h) for h in HEADERS})


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def top_share(values: list[float], n: int) -> float | None:
    if not values:
        return None
    denom = sum(abs(v) for v in values)
    if denom == 0:
        return None
    return sum(abs(v) for v in sorted(values, key=lambda x: abs(x), reverse=True)[:n]) / denom


def bucket_value(value: float | None, buckets: list[tuple[float | None, float | None, str]]) -> str:
    if value is None:
        return "missing"
    for lo, hi, label in buckets:
        if (lo is None or value >= lo) and (hi is None or value < hi):
            return label
    return "other"


def summarize(rows: list[dict], freeze: dict, status: str, reason: str, window: dict) -> dict:
    fills = [r for r in rows if is_true(r.get("entry_fillable"))]
    exits = [r for r in fills if is_true(r.get("exit_liquidity_available"))]
    pnls = [fnum(r.get("net_pnl")) for r in exits]
    pnls = [p for p in pnls if p is not None]
    filled_usd = sum(fnum(r.get("filled_size_usd")) or 0.0 for r in exits)
    by_match: dict[str, float] = defaultdict(float)
    for row in exits:
        pnl = fnum(row.get("net_pnl"))
        if pnl is not None:
            by_match[str(row.get("match_id") or "")] += pnl

    buckets = {
        "source_delay_seconds": ("source_delay_sec", [(0, 2, "0-2s"), (2, 5, "2-5s"), (5, 10, "5-10s"), (10, None, "10s+")]),
        "book_age_seconds": ("book_age_sec", [(0, 0.25, "0-250ms"), (0.25, 1, "250-1000ms"), (1, None, "1s+")]),
        "snapshot_age_seconds": ("snapshot_age_sec", [(0, 2, "0-2s"), (2, 5, "2-5s"), (5, 10, "5-10s"), (10, None, "10s+")]),
    }
    bucket_report = {}
    for name, (field, spec) in buckets.items():
        groups: dict[str, list[dict]] = defaultdict(list)
        for row in exits:
            groups[bucket_value(fnum(row.get(field)), spec)].append(row)
        bucket_report[name] = {
            label: {
                "count": len(grouped),
                "net_pnl": sum(fnum(r.get("net_pnl")) or 0.0 for r in grouped),
            }
            for label, grouped in sorted(groups.items())
        }

    validation_requirements = freeze.get("validation_requirements", {})
    net_pnl = sum(pnls) if pnls else None
    roi = (net_pnl / filled_usd) if net_pnl is not None and filled_usd else None
    exit_liquidity = len(exits) / len(fills) if fills else 0.0
    checks = {
        "minimum_signals": len(rows) >= int(validation_requirements.get("minimum_signals", 100)),
        "minimum_conservative_fills": len(fills) >= int(validation_requirements.get("minimum_conservative_fills", 40)),
        "positive_net_pnl": bool(net_pnl is not None and net_pnl > 0),
        "positive_roi": bool(roi is not None and roi > 0),
        "exit_liquidity": exit_liquidity >= float(validation_requirements.get("minimum_exit_liquidity", 0.80)),
        "not_top5_dominated": (top_share(pnls, 5) is not None and top_share(pnls, 5) < 0.60),
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy": freeze.get("strategy", "quick_exit_stale_book_v1"),
        "status": status,
        "reason": reason,
        "validation_window": window,
        "frozen_rule": {
            "entry_source": freeze.get("entry_source"),
            "fill_model": freeze.get("fill_model"),
            "entry_price": freeze.get("entry_price"),
            "exit_price": freeze.get("exit_price"),
            "exit_horizon_seconds": freeze.get("exit_horizon_seconds"),
            "event_filter": freeze.get("event_filter"),
            "threshold_tuning_allowed": freeze.get("threshold_tuning_allowed"),
        },
        "signals": len(rows),
        "fills": len(fills),
        "fill_rate": len(fills) / len(rows) if rows else 0.0,
        "exits": len(exits),
        "exit_liquidity": exit_liquidity,
        "net_pnl": net_pnl,
        "roi": roi,
        "profit_per_signal": (net_pnl / len(rows)) if net_pnl is not None and rows else None,
        "profit_per_fill": (net_pnl / len(fills)) if net_pnl is not None and fills else None,
        "top_1_trade_pnl_share": top_share(pnls, 1),
        "top_3_trade_pnl_share": top_share(pnls, 3),
        "top_5_trade_pnl_share": top_share(pnls, 5),
        "pnl_by_match_id": dict(sorted(by_match.items(), key=lambda item: abs(item[1]), reverse=True)),
        "latency_source_delay_buckets": bucket_report,
        "checks": checks,
        "pass": status == "validated" and all(checks.values()),
    }


def filter_frozen_rows(rows: list[dict], freeze: dict, start_ns: int | None, end_ns: int | None) -> list[dict]:
    out = []
    for row in rows:
        if row.get("event_family_mode") != "all":
            continue
        if int(float(row.get("exit_horizon_sec") or 0)) != int(freeze.get("exit_horizon_seconds", 30)):
            continue
        ts = parse_ns(row.get("decision_ts"))
        if ts is None:
            continue
        if start_ns is not None and ts < start_ns:
            continue
        if end_ns is not None and ts > end_ns:
            continue
        out.append(row)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", default="reports/quick_exit_stale_book_v1_freeze.json", type=Path)
    parser.add_argument("--replay", default="reports/quick_exit_stale_book_replay.csv", type=Path)
    parser.add_argument("--trades-output", default="reports/quick_exit_stale_book_oos_trades.csv", type=Path)
    parser.add_argument("--report-output", default="reports/quick_exit_stale_book_oos_report.json", type=Path)
    parser.add_argument("--start-ts", help="Inclusive OOS start timestamp, ISO-8601 or ns.")
    parser.add_argument("--end-ts", help="Inclusive OOS end timestamp, ISO-8601 or ns.")
    args = parser.parse_args()

    freeze = json.loads(args.freeze.read_text(encoding="utf-8"))
    start_ns = parse_ns(args.start_ts)
    end_ns = parse_ns(args.end_ts)
    window = {"start_ts": args.start_ts, "end_ts": args.end_ts, "start_ns": start_ns, "end_ns": end_ns}

    if start_ns is None:
        write_csv(args.trades_output, [])
        report = summarize([], freeze, "not_run", "explicit_oos_start_ts_required", window)
        write_json(args.report_output, report)
        print(f"wrote {args.trades_output}")
        print(f"wrote {args.report_output}")
        print(json.dumps({"status": report["status"], "reason": report["reason"]}, sort_keys=True))
        return 2

    rows = filter_frozen_rows(read_csv(args.replay), freeze, start_ns, end_ns)
    write_csv(args.trades_output, rows)
    status = "validated" if rows else "no_oos_rows"
    reason = "completed" if rows else "no_rows_in_requested_oos_window"
    report = summarize(rows, freeze, status, reason, window)
    write_json(args.report_output, report)
    print(f"wrote {args.trades_output}")
    print(f"wrote {args.report_output}")
    print(json.dumps({"status": report["status"], "signals": report["signals"], "fills": report["fills"], "pass": report["pass"]}, sort_keys=True))
    return 0 if rows else 2


if __name__ == "__main__":
    raise SystemExit(main())
