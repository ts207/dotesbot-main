#!/usr/bin/env python3
"""Hard leakage and readiness checks for Model B train/validation data."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def load_locked(path: Path) -> set[str]:
    return {r.get("market_id", "") for r in read_csv(path) if r.get("market_id")}


def fnum(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def count_locked(rows: list[dict[str, str]], locked: set[str]) -> int:
    return sum(r.get("market_id") in locked for r in rows)


def count_bad_market_prob(rows: list[dict[str, str]]) -> int:
    bad = 0
    for row in rows:
        p = fnum(row.get("p_market_early_mid"))
        if p is None or not (0.02 <= p <= 0.98):
            bad += 1
    return bad


def count_low_mapping(rows: list[dict[str, str]]) -> int:
    return sum((fnum(r.get("mapping_confidence")) or 0) < 0.95 for r in rows)


def count_asof_violations(rows: list[dict[str, str]]) -> int:
    bad = 0
    for row in rows:
        proxy = parse_ts(row.get("proxy_ts"))
        decision = parse_ts(row.get("decision_ts"))
        if proxy is not None and decision is not None and proxy > decision:
            bad += 1
        elif row.get("asof_valid") != "True":
            bad += 1
    return bad


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--locked", default="data/locked_execution_audit/locked_market_ids.csv")
    parser.add_argument("--train", default="data/train_pool/train.csv")
    parser.add_argument("--validation", default="data/train_pool/validation.csv")
    parser.add_argument("--output", default="reports/dataset_leakage_checks.json")
    parser.add_argument("--min-ready", type=int, default=150)
    parser.add_argument("--min-train", type=int, default=100)
    parser.add_argument("--min-validation", type=int, default=40)
    args = parser.parse_args()

    locked = load_locked(Path(args.locked))
    train = read_csv(Path(args.train))
    val = read_csv(Path(args.validation))
    train_markets = {r.get("market_id", "") for r in train}
    val_markets = {r.get("market_id", "") for r in val}
    train_games = {r.get("game_id", "") for r in train if r.get("game_id")}
    val_games = {r.get("game_id", "") for r in val if r.get("game_id")}
    train_series = {r.get("series_id", "") for r in train if r.get("series_id")}
    val_series = {r.get("series_id", "") for r in val if r.get("series_id")}
    checks = {
        "locked_rows_in_train": count_locked(train, locked),
        "locked_rows_in_validation": count_locked(val, locked),
        "train_validation_market_overlap": len(train_markets & val_markets),
        "train_validation_game_overlap": len(train_games & val_games),
        "train_validation_series_overlap": len(train_series & val_series),
        "proxy_ts_gt_decision_ts_violations": count_asof_violations(train + val),
        "last_trade_proxy_rows": sum(r.get("market_probability_source") == "last_trade_proxy" for r in train + val),
        "mapping_confidence_violations": count_low_mapping(train + val),
        "p_market_range_violations": count_bad_market_prob(train + val),
        "missing_outcome_violations": sum(r.get("team_a_win") == "" for r in train + val),
        "train_rows": len(train),
        "validation_rows": len(val),
        "non_locked_probability_ready": len(train) + len(val),
        "min_ready_required": args.min_ready,
        "min_train_required": args.min_train,
        "min_validation_required": args.min_validation,
    }
    hard_fail_keys = [
        "locked_rows_in_train",
        "locked_rows_in_validation",
        "train_validation_market_overlap",
        "train_validation_game_overlap",
        "train_validation_series_overlap",
        "proxy_ts_gt_decision_ts_violations",
        "last_trade_proxy_rows",
        "mapping_confidence_violations",
        "p_market_range_violations",
        "missing_outcome_violations",
    ]
    count_fail = (
        checks["non_locked_probability_ready"] < args.min_ready
        or checks["train_rows"] < args.min_train
        or checks["validation_rows"] < args.min_validation
    )
    checks["status"] = "fail" if any(checks[k] for k in hard_fail_keys) or count_fail else "pass"
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(checks, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(checks, indent=2, sort_keys=True))
    if checks["status"] != "pass":
        sys.exit(1)


if __name__ == "__main__":
    main()
