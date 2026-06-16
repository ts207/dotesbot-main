#!/usr/bin/env python3
"""Audit MAP_WINNER market attrition from markets.yaml to analysis set."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def load_book_asset_counts(path: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in read_csv(path):
        asset_id = str(row.get("asset_id") or "")
        if asset_id:
            counts[asset_id] += 1
    return counts


def load_outcomes(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_markets(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [m for m in data.get("markets", []) if m.get("market_type") == "MAP_WINNER"]


def classify(row: dict[str, Any]) -> str:
    if not row["has_digit_match_id"]:
        return "no_digit_match_id"
    if row["in_analysis"]:
        return "analysis"
    if not row["has_book_events"] and not row["has_outcome_cache"]:
        return "no_book_no_outcome"
    if not row["has_book_events"]:
        return "no_book_events"
    if not row["has_outcome_cache"]:
        return "no_outcome_cache"
    if not row["in_clean"]:
        return "book_and_outcome_not_clean"
    return "in_clean_not_analysis"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markets", default="markets.yaml")
    parser.add_argument("--book-events", default="logs/book_events.csv")
    parser.add_argument("--outcomes", default="logs/opendota_outcomes.json")
    parser.add_argument("--clean-matches", default="data/clean/matches.csv")
    parser.add_argument("--output-json", default="reports/attrition_waterfall.json")
    parser.add_argument("--output-csv", default="reports/attrition_waterfall_rows.csv")
    args = parser.parse_args()

    markets = load_markets(Path(args.markets))
    book_counts = load_book_asset_counts(Path(args.book_events))
    outcomes = load_outcomes(Path(args.outcomes))
    clean_by_match = {r["match_id"]: r for r in read_csv(Path(args.clean_matches))}

    rows: list[dict[str, Any]] = []
    for market in markets:
        match_id = str(market.get("dota_match_id") or "")
        has_digit = match_id.isdigit()
        yes_token = str(market.get("yes_token_id") or "")
        no_token = str(market.get("no_token_id") or "")
        yes_book_rows = book_counts[yes_token]
        no_book_rows = book_counts[no_token]
        clean = clean_by_match.get(match_id) if has_digit else None
        row = {
            "market_id": str(market.get("market_id") or ""),
            "name": str(market.get("name") or ""),
            "match_id": match_id,
            "yes_token_id": yes_token,
            "no_token_id": no_token,
            "has_digit_match_id": has_digit,
            "yes_book_rows": yes_book_rows,
            "no_book_rows": no_book_rows,
            "book_rows": yes_book_rows + no_book_rows,
            "has_book_events": (yes_book_rows + no_book_rows) > 0,
            "has_outcome_cache": has_digit and match_id in outcomes,
            "in_clean": clean is not None,
            "clean_has_book": clean is not None and clean.get("has_book") == "1",
            "clean_settled": clean is not None and clean.get("yes_won") in {"0", "1"},
            "in_analysis": (
                clean is not None
                and clean.get("market_type") == "MAP_WINNER"
                and clean.get("has_book") == "1"
                and clean.get("yes_won") in {"0", "1"}
            ),
        }
        row["attrition_bucket"] = classify(row)
        rows.append(row)

    digit_rows = [r for r in rows if r["has_digit_match_id"]]
    summary = {
        "total_map_winner": len(rows),
        "with_digit_match_id": len(digit_rows),
        "digit_with_book_events": sum(r["has_book_events"] for r in digit_rows),
        "digit_with_outcome_cache": sum(r["has_outcome_cache"] for r in digit_rows),
        "digit_with_book_and_outcome_cache": sum(r["has_book_events"] and r["has_outcome_cache"] for r in digit_rows),
        "digit_in_clean": sum(r["in_clean"] for r in digit_rows),
        "digit_clean_has_book": sum(r["clean_has_book"] for r in digit_rows),
        "digit_clean_settled": sum(r["clean_settled"] for r in digit_rows),
        "analysis_set": sum(r["in_analysis"] for r in digit_rows),
        "attrition_buckets": dict(Counter(r["attrition_bucket"] for r in rows)),
        "digit_attrition_buckets": dict(Counter(r["attrition_bucket"] for r in digit_rows)),
        "duplicate_digit_match_ids": {
            k: v for k, v in Counter(r["match_id"] for r in digit_rows).items() if v > 1
        },
    }

    out_json = Path(args.output_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2, sort_keys=True), encoding="utf-8")

    out_csv = Path(args.output_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"wrote {out_json}")
    print(f"wrote {out_csv}")


if __name__ == "__main__":
    main()
