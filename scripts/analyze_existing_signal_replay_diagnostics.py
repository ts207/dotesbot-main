#!/usr/bin/env python3
"""Diagnostic summaries for existing-signal raw replay output."""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_HORIZONS = [5, 10, 20, 30, 60, 120, 180, 300, 600]


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


def avg(rows: list[dict], field: str) -> float | None:
    vals = [fnum(r.get(field)) for r in rows]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def total(rows: list[dict], field: str) -> float | None:
    vals = [fnum(r.get(field)) for r in rows]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def unique_count(rows: list[dict], field: str) -> int:
    return len({r.get(field) for r in rows if r.get(field)})


def detect_horizons(rows: list[dict]) -> list[int]:
    if not rows:
        return DEFAULT_HORIZONS
    headers = rows[0].keys()
    found = []
    for h in DEFAULT_HORIZONS:
        if f"pnl_to_{h}s_markout" in headers:
            found.append(h)
    return found or DEFAULT_HORIZONS


def group_by(rows: list[dict], fields: list[str]) -> dict[tuple, list[dict]]:
    out: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        out[tuple(row.get(f) or "" for f in fields)].append(row)
    return out


def markout_curve(rows: list[dict], horizons: list[int]) -> tuple[list[dict], dict]:
    out = []
    for (assumption,), grouped in sorted(group_by(rows, ["fill_assumption"]).items()):
        filled = [r for r in grouped if is_true(r.get("fillable"))]
        for h in horizons:
            pnl_field = f"pnl_to_{h}s_markout"
            entry_field = f"entry_vs_mid_{h}s"
            pnls = [fnum(r.get(pnl_field)) for r in filled]
            pnls = [p for p in pnls if p is not None]
            moves = [fnum(r.get(entry_field)) for r in filled]
            moves = [m for m in moves if m is not None]
            out.append(
                {
                    "fill_assumption": assumption,
                    "horizon_sec": h,
                    "filled_count": len(filled),
                    "observations": len(pnls),
                    "total_pnl": sum(pnls) if pnls else None,
                    "avg_pnl": (sum(pnls) / len(pnls)) if pnls else None,
                    "positive_count": sum(1 for p in pnls if p > 0),
                    "avg_entry_vs_mid": (sum(moves) / len(moves)) if moves else None,
                }
            )
    return out, {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "horizons_sec": horizons,
        "rows": out,
    }


def by_event_type(rows: list[dict], horizons: list[int]) -> tuple[list[dict], dict]:
    wanted_horizons = [h for h in [10, 30, 60, 120, 300] if h in horizons]
    out = []
    for (assumption, event_type), grouped in sorted(group_by(rows, ["fill_assumption", "event_type"]).items()):
        filled = [r for r in grouped if is_true(r.get("fillable"))]
        row = {
            "fill_assumption": assumption,
            "event_type": event_type,
            "signals": len(grouped),
            "attempts": len(grouped),
            "fills": len(filled),
            "fill_rate": len(filled) / len(grouped) if grouped else 0.0,
            "settlement_pnl": total(filled, "pnl_to_settlement"),
            "avg_spread": avg(grouped, "raw_spread"),
            "avg_depth": avg(grouped, "raw_ask_size"),
            "avg_source_delay": avg(grouped, "source_delay_sec"),
            "avg_latency": avg(grouped, "latency_book_age_at_signal_ms"),
        }
        for h in wanted_horizons:
            row[f"markout_{h}s"] = total(filled, f"pnl_to_{h}s_markout")
        out.append(row)
    return out, {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": out,
    }


def fill_selection(rows: list[dict]) -> dict:
    by_assumption = {}
    metrics = [
        "raw_spread",
        "raw_ask_size",
        "signal_strength",
        "source_delay_sec",
        "latency_book_age_at_signal_ms",
        "initial_staleness",
        "entry_vs_mid_60s",
        "entry_vs_mid_300s",
        "book_age_sec",
        "snapshot_age_sec",
    ]
    for (assumption,), grouped in sorted(group_by(rows, ["fill_assumption"]).items()):
        filled = [r for r in grouped if is_true(r.get("fillable"))]
        unfilled = [r for r in grouped if not is_true(r.get("fillable"))]
        by_assumption[assumption] = {
            "filled_vs_unfilled_count": {
                "filled": len(filled),
                "unfilled": len(unfilled),
                "total": len(grouped),
            },
            "filled": {m: avg(filled, m) for m in metrics},
            "unfilled": {m: avg(unfilled, m) for m in metrics},
        }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "by_fill_assumption": by_assumption,
    }


def top_share(values: list[float], n: int) -> float | None:
    if not values:
        return None
    denom = sum(abs(v) for v in values)
    if denom == 0:
        return None
    return sum(abs(v) for v in sorted(values, key=lambda x: abs(x), reverse=True)[:n]) / denom


def concentration(rows: list[dict], assumption: str) -> dict:
    filled = [r for r in rows if r.get("fill_assumption") == assumption and is_true(r.get("fillable"))]
    pnl_field = "pnl_to_60s_markout"
    trade_pnls = [fnum(r.get(pnl_field)) for r in filled]
    trade_pnls = [p for p in trade_pnls if p is not None]

    by_match: dict[str, float] = defaultdict(float)
    by_market: dict[str, float] = defaultdict(float)
    for r in filled:
        pnl = fnum(r.get(pnl_field))
        if pnl is None:
            continue
        by_match[str(r.get("match_id") or "")] += pnl
        by_market[str(r.get("market_name") or "")] += pnl

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fill_assumption": assumption,
        "pnl_field": pnl_field,
        "filled_count": len(filled),
        "total_pnl": sum(trade_pnls) if trade_pnls else None,
        "top_1_trade_pnl_share": top_share(trade_pnls, 1),
        "top_3_trade_pnl_share": top_share(trade_pnls, 3),
        "top_5_trade_pnl_share": top_share(trade_pnls, 5),
        "top_1_match_pnl_share": top_share(list(by_match.values()), 1),
        "top_3_match_pnl_share": top_share(list(by_match.values()), 3),
        "pnl_by_market_id": dict(sorted(by_market.items(), key=lambda item: abs(item[1]), reverse=True)),
        "pnl_by_match_id": dict(sorted(by_match.items(), key=lambda item: abs(item[1]), reverse=True)),
    }


def bucket_value(value: float | None, buckets: list[tuple[float | None, float | None, str]]) -> str:
    if value is None:
        return "missing"
    for lo, hi, label in buckets:
        if (lo is None or value >= lo) and (hi is None or value < hi):
            return label
    return "other"


def bucket_report(rows: list[dict], assumption: str) -> dict:
    filled = [r for r in rows if r.get("fill_assumption") == assumption and is_true(r.get("fillable"))]
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
    for name, (field, buckets) in specs.items():
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in filled:
            groups[bucket_value(fnum(r.get(field)), buckets)].append(r)
        out[name] = {
            label: {
                "count": len(grouped),
                "pnl_to_60s_markout": total(grouped, "pnl_to_60s_markout"),
                "pnl_to_300s_markout": total(grouped, "pnl_to_300s_markout"),
                "avg_spread": avg(grouped, "raw_spread"),
                "avg_depth": avg(grouped, "raw_ask_size"),
            }
            for label, grouped in sorted(groups.items())
        }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fill_assumption": assumption,
        "buckets": out,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trades", default="reports/replay_existing_signals_trades.csv", type=Path)
    parser.add_argument("--output-dir", default="reports", type=Path)
    parser.add_argument("--primary-assumption", default="conservative_trade_through")
    args = parser.parse_args()

    rows = read_csv(args.trades)
    horizons = detect_horizons(rows)

    curve_rows, curve_json = markout_curve(rows, horizons)
    by_event_rows, by_event_json = by_event_type(rows, horizons)
    fill_selection_json = fill_selection(rows)
    concentration_json = concentration(rows, args.primary_assumption)
    latency_json = bucket_report(rows, args.primary_assumption)

    write_csv(
        args.output_dir / "replay_existing_signals_markout_curve.csv",
        curve_rows,
        ["fill_assumption", "horizon_sec", "filled_count", "observations", "total_pnl", "avg_pnl", "positive_count", "avg_entry_vs_mid"],
    )
    write_json(args.output_dir / "replay_existing_signals_markout_curve.json", curve_json)

    event_headers = [
        "fill_assumption",
        "event_type",
        "signals",
        "attempts",
        "fills",
        "fill_rate",
        "markout_10s",
        "markout_30s",
        "markout_60s",
        "markout_120s",
        "markout_300s",
        "settlement_pnl",
        "avg_spread",
        "avg_depth",
        "avg_source_delay",
        "avg_latency",
    ]
    write_csv(args.output_dir / "replay_existing_signals_by_event_type.csv", by_event_rows, event_headers)
    write_json(args.output_dir / "replay_existing_signals_by_event_type.json", by_event_json)
    write_json(args.output_dir / "replay_existing_signals_fill_selection_audit.json", fill_selection_json)
    write_json(args.output_dir / "replay_existing_signals_concentration.json", concentration_json)
    write_json(args.output_dir / "replay_existing_signals_latency_buckets.json", latency_json)

    print(f"wrote diagnostics to {args.output_dir}")
    print(
        json.dumps(
            {
                "horizons_sec": horizons,
                "primary_assumption": args.primary_assumption,
                "filled_count": concentration_json["filled_count"],
                "primary_total_60s_pnl": concentration_json["total_pnl"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
