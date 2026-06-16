#!/usr/bin/env python3
"""Recover market-level clean rows for mapped games missing from data/clean.

The existing data/clean dataset is match_id keyed. This script writes a separate
market-level recovery dataset so duplicate match_id mappings are visible instead
of overwritten.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


MATCH_HEADERS = [
    "market_id",
    "match_id",
    "market_name",
    "yes_team",
    "no_team",
    "market_type",
    "mapping",
    "duplicate_match_id",
    "snapshots",
    "duration_min",
    "final_kills",
    "final_yes_nw",
    "has_book",
    "outcome_radiant_won",
    "yes_won",
    "recover_status",
    "recover_reason",
]

SNAPSHOT_HEADERS = [
    "timestamp_utc",
    "gt_sec",
    "gt_min",
    "yes_score",
    "no_score",
    "yes_kill_lead",
    "yes_nw_lead",
    "yes_lead_per_min",
    "yes_bid",
    "yes_ask",
    "yes_spread",
    "no_bid",
    "no_ask",
    "no_spread",
]


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def fnum(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def load_markets(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [m for m in data.get("markets", []) if m.get("market_type") == "MAP_WINNER"]


def load_outcomes(path: Path) -> dict[str, bool]:
    if not path.exists():
        return {}
    return {str(k): bool(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def load_book_ticks(path: Path, assets: set[str]) -> dict[str, list[dict[str, Any]]]:
    books: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_csv(path):
        asset_id = str(row.get("asset_id") or "")
        if asset_id not in assets:
            continue
        ts = parse_ts(row.get("timestamp_utc"))
        bid = fnum(row.get("best_bid"))
        ask = fnum(row.get("best_ask"))
        spread = fnum(row.get("spread"))
        if ts is None:
            continue
        books[asset_id].append({"ts": ts, "bid": bid, "ask": ask, "spread": spread})
    for rows in books.values():
        rows.sort(key=lambda r: r["ts"])
    return books


def load_snapshots(path: Path, match_ids: set[str]) -> dict[str, list[dict[str, Any]]]:
    snapshots: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_csv(path):
        match_id = str(row.get("match_id") or "")
        if match_id not in match_ids:
            continue
        ts = parse_ts(row.get("received_at_utc"))
        gt = fnum(row.get("game_time_sec"))
        lead = fnum(row.get("radiant_lead"))
        radiant_score = fnum(row.get("radiant_score"))
        dire_score = fnum(row.get("dire_score"))
        if ts is None or gt is None or lead is None:
            continue
        snapshots[match_id].append(
            {
                "ts": ts,
                "timestamp_utc": row.get("received_at_utc") or "",
                "gt_sec": gt,
                "radiant_lead": lead,
                "radiant_score": radiant_score,
                "dire_score": dire_score,
            }
        )
    for rows in snapshots.values():
        rows.sort(key=lambda r: (r["gt_sec"], r["ts"]))
    return snapshots


def latest_book_at_or_before(rows: list[dict[str, Any]], ts: datetime) -> dict[str, Any] | None:
    latest = None
    for row in rows:
        if row["ts"] <= ts:
            latest = row
        else:
            break
    return latest


def fmt(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.4f}".rstrip("0").rstrip(".")


def build_market_snapshots(
    market: dict[str, Any],
    snapshots: list[dict[str, Any]],
    yes_books: list[dict[str, Any]],
    no_books: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    mapping = str(market.get("steam_side_mapping") or "")
    reversed_side = mapping == "reversed"
    rows = []
    seen = set()
    for snap in snapshots:
        yes_book = latest_book_at_or_before(yes_books, snap["ts"])
        no_book = latest_book_at_or_before(no_books, snap["ts"])
        radiant_score = snap.get("radiant_score")
        dire_score = snap.get("dire_score")
        if reversed_side:
            yes_score, no_score = dire_score, radiant_score
            yes_nw = -float(snap["radiant_lead"])
        else:
            yes_score, no_score = radiant_score, dire_score
            yes_nw = float(snap["radiant_lead"])
        gt_sec = float(snap["gt_sec"])
        key = (round(gt_sec, 3), fmt(yes_book.get("ask") if yes_book else None), fmt(no_book.get("ask") if no_book else None))
        if key in seen:
            continue
        seen.add(key)
        gt_min = gt_sec / 60.0
        rows.append(
            {
                "timestamp_utc": snap["timestamp_utc"],
                "gt_sec": int(gt_sec) if gt_sec.is_integer() else round(gt_sec, 3),
                "gt_min": round(gt_min, 2),
                "yes_score": int(yes_score) if yes_score is not None else "",
                "no_score": int(no_score) if no_score is not None else "",
                "yes_kill_lead": int((yes_score or 0) - (no_score or 0)) if yes_score is not None and no_score is not None else "",
                "yes_nw_lead": int(round(yes_nw)),
                "yes_lead_per_min": round(yes_nw / max(gt_min, 1.0), 2),
                "yes_bid": fmt(yes_book.get("bid") if yes_book else None),
                "yes_ask": fmt(yes_book.get("ask") if yes_book else None),
                "yes_spread": fmt(yes_book.get("spread") if yes_book else None),
                "no_bid": fmt(no_book.get("bid") if no_book else None),
                "no_ask": fmt(no_book.get("ask") if no_book else None),
                "no_spread": fmt(no_book.get("spread") if no_book else None),
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attrition", default="reports/attrition_waterfall_rows.csv")
    parser.add_argument("--markets", default="markets.yaml")
    parser.add_argument("--outcomes", default="logs/opendota_outcomes.json")
    parser.add_argument("--raw-snapshots", default="logs/raw_snapshots.csv")
    parser.add_argument("--book-events", default="logs/book_events.csv")
    parser.add_argument("--output-dir", default="data/clean_recovered")
    parser.add_argument("--min-snapshots", type=int, default=3)
    parser.add_argument("--min-book-rows", type=int, default=10)
    args = parser.parse_args()

    attrition = read_csv(Path(args.attrition))
    wanted_ids = {
        r["market_id"]
        for r in attrition
        if r.get("attrition_bucket") in {"book_and_outcome_not_clean", "in_clean_not_analysis"}
    }
    markets = [m for m in load_markets(Path(args.markets)) if str(m.get("market_id") or "") in wanted_ids]
    match_id_counts = Counter(str(m.get("dota_match_id") or "") for m in markets)
    outcomes = load_outcomes(Path(args.outcomes))
    assets = {str(m.get("yes_token_id") or "") for m in markets} | {str(m.get("no_token_id") or "") for m in markets}
    books = load_book_ticks(Path(args.book_events), assets)
    snapshots_by_match = load_snapshots(Path(args.raw_snapshots), {str(m.get("dota_match_id") or "") for m in markets})

    out_dir = Path(args.output_dir)
    snap_dir = out_dir / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)

    manifest_rows = []
    for market in markets:
        market_id = str(market.get("market_id") or "")
        match_id = str(market.get("dota_match_id") or "")
        yes_token = str(market.get("yes_token_id") or "")
        no_token = str(market.get("no_token_id") or "")
        yes_books = books.get(yes_token, [])
        no_books = books.get(no_token, [])
        snaps = snapshots_by_match.get(match_id, [])
        duplicate = match_id_counts[match_id] > 1
        reason = []
        if len(snaps) < args.min_snapshots:
            reason.append("insufficient_snapshots")
        if len(yes_books) + len(no_books) < args.min_book_rows:
            reason.append("insufficient_book_rows")
        if match_id not in outcomes:
            reason.append("missing_outcome")
        if str(market.get("steam_side_mapping") or "") not in {"normal", "reversed"}:
            reason.append("missing_mapping")
        status = "recovered" if not reason else "diagnostic_only"

        recovered = build_market_snapshots(market, snaps, yes_books, no_books) if status == "recovered" else []
        if recovered:
            with (snap_dir / f"{market_id}_{match_id}.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=SNAPSHOT_HEADERS)
                writer.writeheader()
                writer.writerows(recovered)

        final = recovered[-1] if recovered else {}
        radiant_win = outcomes.get(match_id)
        mapping = str(market.get("steam_side_mapping") or "")
        yes_won = None
        if radiant_win is not None and mapping in {"normal", "reversed"}:
            yes_won = bool(radiant_win) if mapping == "normal" else not bool(radiant_win)
        manifest_rows.append(
            {
                "market_id": market_id,
                "match_id": match_id,
                "market_name": str(market.get("name") or ""),
                "yes_team": str(market.get("yes_team") or ""),
                "no_team": str(market.get("no_team") or ""),
                "market_type": str(market.get("market_type") or ""),
                "mapping": mapping,
                "duplicate_match_id": int(duplicate),
                "snapshots": len(recovered) if recovered else len(snaps),
                "duration_min": final.get("gt_min", ""),
                "final_kills": f"{final.get('yes_score', '')}-{final.get('no_score', '')}" if final else "",
                "final_yes_nw": final.get("yes_nw_lead", ""),
                "has_book": int(bool(yes_books and no_books)),
                "outcome_radiant_won": int(radiant_win) if radiant_win is not None else "",
                "yes_won": int(yes_won) if yes_won is not None else "",
                "recover_status": status,
                "recover_reason": ",".join(reason),
            }
        )

    with (out_dir / "matches.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MATCH_HEADERS)
        writer.writeheader()
        writer.writerows(manifest_rows)

    summary = {
        "candidate_markets": len(markets),
        "recovered": sum(r["recover_status"] == "recovered" for r in manifest_rows),
        "diagnostic_only": sum(r["recover_status"] != "recovered" for r in manifest_rows),
        "duplicate_match_id_rows": sum(int(r["duplicate_match_id"]) for r in manifest_rows),
        "status_reasons": dict(Counter(r["recover_reason"] or "recovered" for r in manifest_rows)),
    }
    (out_dir / "recovery_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"wrote {out_dir / 'matches.csv'}")
    print(f"wrote {snap_dir}")


if __name__ == "__main__":
    main()
