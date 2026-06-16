#!/usr/bin/env python3
"""Analyze quick-exit stale-book replay outputs."""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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


def write_csv(path: Path, rows: list[dict], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({h: row.get(h) for h in headers})


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def group_by(rows: list[dict], fields: list[str]) -> dict[tuple, list[dict]]:
    out: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        out[tuple(str(row.get(f) or "") for f in fields)].append(row)
    return out


def avg(rows: list[dict], field: str) -> float | None:
    vals = [fnum(r.get(field)) for r in rows]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def total(rows: list[dict], field: str) -> float | None:
    vals = [fnum(r.get(field)) for r in rows]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def max_drawdown(rows: list[dict]) -> float:
    ordered = sorted(rows, key=lambda r: int(float(r.get("decision_ts") or 0)))
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for row in ordered:
        equity += fnum(row.get("net_pnl")) or 0.0
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def top_share(values: list[float], n: int) -> float | None:
    if not values:
        return None
    denom = sum(abs(v) for v in values)
    if denom == 0:
        return None
    return sum(abs(v) for v in sorted(values, key=lambda x: abs(x), reverse=True)[:n]) / denom


def summary_row(rows: list[dict], mode: str, horizon: str, extra: dict | None = None) -> dict:
    fills = [r for r in rows if is_true(r.get("entry_fillable"))]
    exits = [r for r in fills if is_true(r.get("exit_liquidity_available"))]
    net = total(exits, "net_pnl")
    gross = total(exits, "gross_pnl")
    filled_usd = total(exits, "filled_size_usd") or 0.0
    pnls = [fnum(r.get("net_pnl")) for r in exits]
    pnls = [p for p in pnls if p is not None]
    out = {
        "event_family_mode": mode,
        "exit_horizon_sec": horizon,
        "signals": len(rows),
        "attempts": len(rows),
        "fills": len(fills),
        "fill_rate": len(fills) / len(rows) if rows else 0.0,
        "exits": len(exits),
        "exit_liquidity_rate": len(exits) / len(fills) if fills else 0.0,
        "gross_pnl": gross,
        "net_pnl": net,
        "roi": (net / filled_usd) if net is not None and filled_usd else None,
        "avg_entry_price": avg(exits, "entry_price"),
        "avg_exit_price": avg(exits, "exit_bid"),
        "avg_spread": avg(rows, "entry_spread"),
        "avg_depth": avg(rows, "entry_depth_usd"),
        "avg_source_delay": avg(rows, "source_delay_sec"),
        "avg_snapshot_age": avg(rows, "snapshot_age_sec"),
        "avg_book_age": avg(rows, "book_age_sec"),
        "max_drawdown": max_drawdown(exits),
        "profit_per_signal": (net / len(rows)) if net is not None and rows else None,
        "profit_per_fill": (net / len(fills)) if net is not None and fills else None,
        "top_1_trade_pnl_share": top_share(pnls, 1),
        "top_3_trade_pnl_share": top_share(pnls, 3),
        "top_5_trade_pnl_share": top_share(pnls, 5),
    }
    if extra:
        out.update(extra)
    return out


def summary(rows: list[dict]) -> tuple[list[dict], dict]:
    out = []
    by_combo = group_by(rows, ["event_family_mode", "exit_horizon_sec"])
    for (mode, horizon), grouped in sorted(by_combo.items(), key=lambda item: (item[0][0], int(item[0][1]))):
        out.append(summary_row(grouped, mode, horizon))
    return out, {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy_name": "quick_exit_stale_book_v1",
        "exit_model": "ask_entry_bid_exit",
        "by_mode_horizon": out,
    }


def by_event_family(rows: list[dict]) -> list[dict]:
    out = []
    grouped = group_by(rows, ["event_family_mode", "exit_horizon_sec", "event_type"])
    for (mode, horizon, event_type), group in sorted(grouped.items(), key=lambda item: (item[0][0], int(item[0][1]), item[0][2])):
        out.append(summary_row(group, mode, horizon, {"event_type": event_type}))
    return out


def concentration(rows: list[dict]) -> dict:
    out = {}
    for (mode, horizon), grouped in sorted(group_by(rows, ["event_family_mode", "exit_horizon_sec"]).items()):
        exits = [r for r in grouped if is_true(r.get("entry_fillable")) and is_true(r.get("exit_liquidity_available"))]
        pnls = [fnum(r.get("net_pnl")) for r in exits]
        pnls = [p for p in pnls if p is not None]
        by_match: dict[str, float] = defaultdict(float)
        by_market: dict[str, float] = defaultdict(float)
        for row in exits:
            pnl = fnum(row.get("net_pnl"))
            if pnl is None:
                continue
            by_match[str(row.get("match_id") or "")] += pnl
            by_market[str(row.get("market_name") or "")] += pnl
        key = f"{mode}_{horizon}s"
        out[key] = {
            "event_family_mode": mode,
            "exit_horizon_sec": int(horizon),
            "exits": len(exits),
            "total_net_pnl": sum(pnls) if pnls else None,
            "top_1_trade_pnl_share": top_share(pnls, 1),
            "top_3_trade_pnl_share": top_share(pnls, 3),
            "top_5_trade_pnl_share": top_share(pnls, 5),
            "top_1_match_pnl_share": top_share(list(by_match.values()), 1),
            "top_3_match_pnl_share": top_share(list(by_match.values()), 3),
            "pnl_by_market_id": dict(sorted(by_market.items(), key=lambda item: abs(item[1]), reverse=True)),
            "pnl_by_match_id": dict(sorted(by_match.items(), key=lambda item: abs(item[1]), reverse=True)),
        }
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "by_mode_horizon": out}


def bucket_value(value: float | None, buckets: list[tuple[float | None, float | None, str]]) -> str:
    if value is None:
        return "missing"
    for lo, hi, label in buckets:
        if (lo is None or value >= lo) and (hi is None or value < hi):
            return label
    return "other"


def latency_buckets(rows: list[dict]) -> dict:
    specs = {
        "source_delay_seconds": (
            "source_delay_sec",
            [(0, 2, "0-2s"), (2, 5, "2-5s"), (5, 10, "5-10s"), (10, None, "10s+")],
        ),
        "book_latency_ms": (
            "latency_book_age_at_signal_ms",
            [(0, 250, "0-250ms"), (250, 1000, "250-1000ms"), (1000, None, "1s+")],
        ),
        "snapshot_age_seconds": (
            "snapshot_age_sec",
            [(0, 2, "0-2s"), (2, 5, "2-5s"), (5, 10, "5-10s"), (10, None, "10s+")],
        ),
        "book_age_seconds": (
            "book_age_sec",
            [(0, 0.25, "0-250ms"), (0.25, 1, "250-1000ms"), (1, None, "1s+")],
        ),
    }
    out = {}
    for (mode, horizon), grouped in sorted(group_by(rows, ["event_family_mode", "exit_horizon_sec"]).items()):
        exits = [r for r in grouped if is_true(r.get("entry_fillable")) and is_true(r.get("exit_liquidity_available"))]
        key = f"{mode}_{horizon}s"
        out[key] = {}
        for name, (field, buckets) in specs.items():
            bucketed: dict[str, list[dict]] = defaultdict(list)
            for row in exits:
                bucketed[bucket_value(fnum(row.get(field)), buckets)].append(row)
            out[key][name] = {
                label: {
                    "count": len(bucket_rows),
                    "net_pnl": total(bucket_rows, "net_pnl"),
                    "roi": ((total(bucket_rows, "net_pnl") or 0.0) / (total(bucket_rows, "filled_size_usd") or 0.0))
                    if total(bucket_rows, "filled_size_usd")
                    else None,
                    "avg_spread": avg(bucket_rows, "entry_spread"),
                    "avg_depth": avg(bucket_rows, "entry_depth_usd"),
                }
                for label, bucket_rows in sorted(bucketed.items())
            }
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "by_mode_horizon": out}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", default="reports/quick_exit_stale_book_replay.csv", type=Path)
    parser.add_argument("--output-dir", default="reports", type=Path)
    args = parser.parse_args()

    rows = read_csv(args.replay)
    summary_rows, summary_json = summary(rows)
    event_rows = by_event_family(rows)
    concentration_json = concentration(rows)
    latency_json = latency_buckets(rows)

    summary_headers = [
        "event_family_mode",
        "exit_horizon_sec",
        "signals",
        "attempts",
        "fills",
        "fill_rate",
        "exits",
        "exit_liquidity_rate",
        "gross_pnl",
        "net_pnl",
        "roi",
        "avg_entry_price",
        "avg_exit_price",
        "avg_spread",
        "avg_depth",
        "avg_source_delay",
        "avg_snapshot_age",
        "avg_book_age",
        "max_drawdown",
        "profit_per_signal",
        "profit_per_fill",
        "top_1_trade_pnl_share",
        "top_3_trade_pnl_share",
        "top_5_trade_pnl_share",
    ]
    event_headers = ["event_type", *summary_headers]

    write_json(args.output_dir / "quick_exit_stale_book_report.json", summary_json)
    write_csv(args.output_dir / "quick_exit_stale_book_by_event_family.csv", event_rows, event_headers)
    write_json(args.output_dir / "quick_exit_stale_book_concentration.json", concentration_json)
    write_json(args.output_dir / "quick_exit_stale_book_latency_buckets.json", latency_json)

    print(f"wrote quick-exit analysis to {args.output_dir}")
    print(json.dumps({"combinations": len(summary_rows), "rows": len(rows)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
