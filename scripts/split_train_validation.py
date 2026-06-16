#!/usr/bin/env python3
"""Temporal series-disjoint split for non-locked Model B candidates."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

V2_REQUIRED_COLUMNS = [
    "team_strength_diff",
    "team_recent_form_diff",
    "team_strength_feature_confidence",
    "v2_no_leak_valid",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def sort_key(row: dict[str, str]) -> tuple[int, str]:
    raw = row.get("start_ts") or row.get("decision_ts") or ""
    try:
        # start_ts is often unix seconds in current OpenDota cache.
        ts = int(float(raw))
    except (TypeError, ValueError):
        try:
            ts = int(datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp())
        except ValueError:
            ts = 0
    return ts, row.get("market_id", "")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["v1", "v2"], default="v1")
    parser.add_argument("--candidates", default=None)
    parser.add_argument("--train-output", default=None)
    parser.add_argument("--validation-output", default=None)
    parser.add_argument("--report-output", default=None)
    parser.add_argument("--train-frac", type=float, default=0.75)
    args = parser.parse_args()
    if args.mode == "v2":
        args.candidates = args.candidates or "data/train_pool/model_b_v2_candidates.csv"
        args.train_output = args.train_output or "data/train_pool/train_v2.csv"
        args.validation_output = args.validation_output or "data/train_pool/validation_v2.csv"
        args.report_output = args.report_output or "reports/train_validation_split_v2_report.json"
    else:
        args.candidates = args.candidates or "data/train_pool/model_b_candidates.csv"
        args.train_output = args.train_output or "data/train_pool/train.csv"
        args.validation_output = args.validation_output or "data/train_pool/validation.csv"
        args.report_output = args.report_output or "reports/train_validation_split_report.json"

    rows = [r for r in read_csv(Path(args.candidates)) if r.get("dataset_role") == "train_pool_candidate"]
    if args.mode == "v2":
        headers = set(rows[0].keys()) if rows else set()
        missing = [col for col in V2_REQUIRED_COLUMNS if col not in headers]
        if missing:
            raise SystemExit(f"v2 split requires v2 candidate columns, missing: {missing}")
    rows.sort(key=sort_key)
    series_order = []
    seen = set()
    by_series: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        series = row.get("series_id") or row.get("match_id") or row.get("market_id")
        row["series_id_source"] = "series_id" if row.get("series_id") else "match_id" if row.get("match_id") else "market_id"
        by_series.setdefault(series, []).append(row)
        if series not in seen:
            series_order.append(series)
            seen.add(series)
    split_idx = int(len(series_order) * args.train_frac)
    train_series = set(series_order[:split_idx])
    train = [r for s in series_order if s in train_series for r in by_series[s]]
    val = [r for s in series_order if s not in train_series for r in by_series[s]]
    headers = list(rows[0].keys()) if rows else []
    write_csv(Path(args.train_output), train, headers)
    write_csv(Path(args.validation_output), val, headers)
    report = {
        "mode": args.mode,
        "candidate_file": args.candidates,
        "candidate_rows": len(rows),
        "series_count": len(series_order),
        "train_rows": len(train),
        "validation_rows": len(val),
        "train_series": len(train_series),
        "validation_series": len(series_order) - len(train_series),
        "series_overlap": len(set(r.get("series_id") for r in train) & set(r.get("series_id") for r in val)),
        "market_overlap": len(set(r.get("market_id") for r in train) & set(r.get("market_id") for r in val)),
        "game_overlap": len(set(r.get("game_id") for r in train) & set(r.get("game_id") for r in val)),
        "v2_required_columns_present": all(col in (rows[0].keys() if rows else []) for col in V2_REQUIRED_COLUMNS)
        if args.mode == "v2"
        else None,
    }
    Path(args.report_output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report_output).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"wrote {args.train_output}")
    print(f"wrote {args.validation_output}")
    print(f"wrote {args.report_output}")


if __name__ == "__main__":
    main()
