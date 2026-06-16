#!/usr/bin/env python3
"""Freeze and reconcile the locked execution-audit market set."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml


LOCKED_HEADERS = [
    "market_id",
    "condition_id",
    "match_id",
    "game_id",
    "series_id",
    "yes_team_id",
    "no_team_id",
    "locked_reason",
]


RECON_HEADERS = [
    "market_id",
    "condition_id",
    "match_id",
    "quality_status",
    "anchor_source",
    "team_a_id",
    "team_b_id",
    "locked_status",
    "reconciliation_class",
    "reconciliation_note",
]


SUMMARY_HEADERS = ["metric", "value"]


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


def load_existing_locked(path: Path) -> list[dict[str, str]]:
    rows = read_csv(path)
    return [r for r in rows if r.get("market_id")]


def build_from_clean_v2(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    locked = []
    for row in rows:
        if row.get("quality_status") != "analysis_ready":
            continue
        locked.append(
            {
                "market_id": row.get("market_id", ""),
                "condition_id": row.get("condition_id", ""),
                "match_id": row.get("match_id", ""),
                "game_id": row.get("market_id", ""),
                "series_id": row.get("match_id", ""),
                "yes_team_id": row.get("team_a_id", ""),
                "no_team_id": row.get("team_b_id", ""),
                "locked_reason": "existing_booktick_execution_audit",
            }
        )
    return locked


def load_raw_market_duplicates(path: Path, locked_ids: set[str]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for market in data.get("markets", []):
        if market.get("market_type") != "MAP_WINNER":
            continue
        key = (
            str(market.get("market_id") or ""),
            str(market.get("condition_id") or ""),
            str(market.get("yes_token_id") or ""),
            str(market.get("no_token_id") or ""),
        )
        if not key[0]:
            continue
        grouped.setdefault(key, []).append(market)
    duplicate_rows = []
    for (market_id, condition_id, _yes, _no), rows in grouped.items():
        if len(rows) <= 1:
            continue
        if locked_ids and market_id not in locked_ids:
            continue
        match_id = str(rows[0].get("dota_match_id") or "")
        for _ in range(len(rows) - 1):
            duplicate_rows.append(
                {
                    "market_id": market_id,
                    "condition_id": condition_id,
                    "match_id": match_id,
                    "quality_status": "",
                    "anchor_source": "",
                    "team_a_id": "",
                    "team_b_id": "",
                    "locked_status": "missing_expected",
                    "reconciliation_class": "duplicate_removed",
                    "reconciliation_note": "exact duplicate raw Polymarket record removed by dedupe key",
                }
            )
    return duplicate_rows


def reconcile(
    clean_rows: list[dict[str, str]],
    locked_rows: list[dict[str, str]],
    expected_count: int,
    markets_path: Path,
) -> tuple[list[dict[str, str]], dict[str, int]]:
    clean_by_market = {r.get("market_id", ""): r for r in clean_rows if r.get("market_id")}
    recon = []
    locked_ids = [r.get("market_id", "") for r in locked_rows if r.get("market_id")]
    id_counts = Counter(locked_ids)
    for locked in locked_rows:
        market_id = locked.get("market_id", "")
        clean = clean_by_market.get(market_id, {})
        notes = []
        klass = "materialized"
        if not clean:
            klass = "not_in_current_local_files"
            notes.append("missing_from_clean_v2")
        elif clean.get("quality_status") != "analysis_ready":
            klass = "bad_or_ambiguous_mapping"
            notes.append("not_analysis_ready")
        if id_counts[market_id] > 1:
            klass = "duplicate_removed"
            notes.append("duplicate_locked_market_id")
        recon.append(
            {
                "market_id": market_id,
                "condition_id": locked.get("condition_id", "") or clean.get("condition_id", ""),
                "match_id": locked.get("match_id", "") or clean.get("match_id", ""),
                "quality_status": clean.get("quality_status", ""),
                "anchor_source": clean.get("anchor_source", ""),
                "team_a_id": clean.get("team_a_id", ""),
                "team_b_id": clean.get("team_b_id", ""),
                "locked_status": "locked",
                "reconciliation_class": klass,
                "reconciliation_note": ",".join(notes) if notes else "ok",
            }
        )

    missing = max(expected_count - len(locked_rows), 0)
    duplicate_candidates = load_raw_market_duplicates(markets_path, set(locked_ids))
    for row in duplicate_candidates[:missing]:
        recon.append(row)
    remaining = missing - min(missing, len(duplicate_candidates))
    for _ in range(remaining):
        recon.append(
            {
                "market_id": "",
                "condition_id": "",
                "match_id": "",
                "quality_status": "",
                "anchor_source": "",
                "team_a_id": "",
                "team_b_id": "",
                "locked_status": "missing_expected",
                "reconciliation_class": "not_in_current_local_files",
                "reconciliation_note": "expected locked row has no reconstructable local market metadata",
            }
        )

    class_counts = Counter(r.get("reconciliation_class", "") for r in recon)
    summary = {
        "locked_execution_audit_expected": expected_count,
        "locked_execution_audit_materialized": len(locked_rows),
        "locked_missing_or_unresolved": missing,
        **{f"locked_reconciliation_{k}": v for k, v in class_counts.items() if k},
    }
    return recon, summary


def write_summary(path: Path, summary: dict[str, int]) -> None:
    write_csv(path, [{"metric": k, "value": str(v)} for k, v in summary.items()], SUMMARY_HEADERS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean-v2", default="data/clean_v2/matches.csv")
    parser.add_argument("--markets", default="markets.yaml")
    parser.add_argument("--locked-output", default="data/locked_execution_audit/locked_market_ids.csv")
    parser.add_argument("--reconciliation-output", default="reports/locked_set_reconciliation.csv")
    parser.add_argument("--summary-output", default="reports/locked_set_summary.json")
    parser.add_argument("--expected-count", type=int, default=103)
    parser.add_argument(
        "--preserve-existing",
        action="store_true",
        help="If locked output already exists, reconcile it instead of rebuilding from clean_v2.",
    )
    args = parser.parse_args()

    clean_rows = read_csv(Path(args.clean_v2))
    locked_path = Path(args.locked_output)
    if args.preserve_existing and locked_path.exists():
        locked_rows = load_existing_locked(locked_path)
    else:
        locked_rows = build_from_clean_v2(clean_rows)
        write_csv(locked_path, locked_rows, LOCKED_HEADERS)

    recon, summary = reconcile(clean_rows, locked_rows, args.expected_count, Path(args.markets))
    write_csv(Path(args.reconciliation_output), recon, RECON_HEADERS)
    Path(args.summary_output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.summary_output).write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"wrote {locked_path}")
    print(f"wrote {args.reconciliation_output}")
    print(f"wrote {args.summary_output}")


if __name__ == "__main__":
    main()
