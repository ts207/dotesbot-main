#!/usr/bin/env python3
"""Build market_id-keyed clean_v2 dataset for MAP_WINNER markets."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from recover_missing_games import (
    SNAPSHOT_HEADERS,
    build_market_snapshots,
    load_book_ticks,
    load_outcomes,
    load_snapshots,
)


HEADERS = [
    "market_id",
    "match_id",
    "condition_id",
    "token_id_yes",
    "token_id_no",
    "team_a",
    "team_b",
    "team_a_id",
    "team_b_id",
    "team_a_is_radiant",
    "tournament_name",
    "game_number",
    "team_a_win",
    "draft_completed_ts",
    "draft_completion_source",
    "earliest_book_ts",
    "earliest_book_mid",
    "anchor_source",
    "snapshots",
    "has_book",
    "quality_status",
    "quality_reason",
]


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_markets(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = []
    seen = set()
    for market in data.get("markets", []):
        if market.get("market_type") != "MAP_WINNER":
            continue
        match_id = str(market.get("dota_match_id") or "")
        if not match_id.isdigit():
            continue
        key = (
            str(market.get("market_id") or ""),
            str(market.get("condition_id") or ""),
            str(market.get("yes_token_id") or ""),
            str(market.get("no_token_id") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(market)
    return out


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def load_existing_clean(path: Path) -> dict[str, dict[str, str]]:
    rows = read_csv(path)
    return {
        r["match_id"]: r
        for r in rows
        if r.get("market_type") == "MAP_WINNER" and r.get("has_book") == "1" and r.get("yes_won") in {"0", "1"}
    }


def market_book_presence(market: dict[str, Any], books: dict[str, list[dict[str, Any]]]) -> bool:
    return bool(books.get(str(market.get("yes_token_id") or "")) and books.get(str(market.get("no_token_id") or "")))


def earliest_book(yes_books: list[dict[str, Any]], no_books: list[dict[str, Any]]) -> tuple[str, str]:
    pairs = []
    for row in yes_books:
        if row.get("ask") is not None or row.get("bid") is not None:
            pairs.append(row)
    if not pairs:
        return "", ""
    first = min(pairs, key=lambda r: r["ts"])
    bid = first.get("bid")
    ask = first.get("ask")
    mid = ""
    if bid is not None and ask is not None and ask >= bid:
        mid = f"{(bid + ask) / 2:.4f}".rstrip("0").rstrip(".")
    return first["ts"].isoformat(), mid


def game_number(name: str) -> str:
    match = re.search(r"\bGame\s+(\d+)\b", name, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def tournament_name(market: dict[str, Any]) -> str:
    url = str(market.get("source_url") or "")
    if "/dota-2/" in url:
        parts = url.split("/dota-2/", 1)[1].split("/")
        if parts:
            return parts[0].replace("-", " ").title()
    return ""


def draft_completed_ts(match: dict[str, Any] | None) -> tuple[str, str]:
    if not match:
        return "", "missing_match_detail"
    picks_bans = match.get("picks_bans") or []
    timings = [x.get("timings") for x in picks_bans if x.get("timings") is not None]
    if not timings:
        return "", "picks_bans_no_timings"
    start = match.get("start_time")
    if start is None:
        return "", "missing_start_time"
    last = max(float(v) for v in timings)
    ts = datetime.fromtimestamp(float(start), tz=timezone.utc) + timedelta(seconds=last)
    return ts.isoformat(), "picks_bans_timings"


def team_ids(market: dict[str, Any], match: dict[str, Any] | None) -> tuple[str, str]:
    if not match:
        return "", ""
    mapping = str(market.get("steam_side_mapping") or "")
    radiant_id = str(match.get("radiant_team_id") or "")
    dire_id = str(match.get("dire_team_id") or "")
    if mapping == "normal":
        return radiant_id, dire_id
    if mapping == "reversed":
        return dire_id, radiant_id
    return "", ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markets", default="markets.yaml")
    parser.add_argument("--outcomes", default="logs/opendota_outcomes.json")
    parser.add_argument("--match-details", default="logs/opendota_player_match_details.json")
    parser.add_argument("--raw-snapshots", default="logs/raw_snapshots.csv")
    parser.add_argument("--book-events", default="logs/book_events.csv")
    parser.add_argument("--clean-matches", default="data/clean/matches.csv")
    parser.add_argument("--clean-snapshots-dir", default="data/clean/snapshots")
    parser.add_argument("--output-dir", default="data/clean_v2")
    parser.add_argument("--min-snapshots", type=int, default=3)
    parser.add_argument("--min-book-rows", type=int, default=10)
    args = parser.parse_args()

    markets = load_markets(Path(args.markets))
    outcomes = load_outcomes(Path(args.outcomes))
    details = json.loads(Path(args.match_details).read_text(encoding="utf-8")) if Path(args.match_details).exists() else {}
    existing_clean = load_existing_clean(Path(args.clean_matches))
    clean_snapshots_dir = Path(args.clean_snapshots_dir)

    assets = {str(m.get("yes_token_id") or "") for m in markets} | {str(m.get("no_token_id") or "") for m in markets}
    books = load_book_ticks(Path(args.book_events), assets)
    match_ids = {str(m.get("dota_match_id") or "") for m in markets}
    snapshots_by_match = load_snapshots(Path(args.raw_snapshots), match_ids)

    out_dir = Path(args.output_dir)
    snap_dir = out_dir / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)

    match_id_counts = Counter(str(m.get("dota_match_id") or "") for m in markets)
    rows = []
    for market in markets:
        market_id = str(market.get("market_id") or "")
        match_id = str(market.get("dota_match_id") or "")
        mapping = str(market.get("steam_side_mapping") or "")
        yes_books = books.get(str(market.get("yes_token_id") or ""), [])
        no_books = books.get(str(market.get("no_token_id") or ""), [])
        snaps = snapshots_by_match.get(match_id, [])
        radiant_win = outcomes.get(match_id)
        reasons = []
        if radiant_win is None:
            reasons.append("missing_outcome")
        if mapping not in {"normal", "reversed"}:
            reasons.append("missing_mapping")
        if len(yes_books) + len(no_books) < args.min_book_rows:
            reasons.append("insufficient_book_rows")
        if len(snaps) < args.min_snapshots:
            reasons.append("insufficient_snapshots")

        market_snaps = []
        if not reasons:
            market_snaps = build_market_snapshots(market, snaps, yes_books, no_books)
            if len(market_snaps) < args.min_snapshots:
                reasons.append("insufficient_projected_snapshots")

        status = "analysis_ready" if not reasons else "diagnostic_only"
        clean_source = False
        if status != "analysis_ready" and match_id_counts[match_id] == 1 and match_id in existing_clean:
            clean_snapshot_path = clean_snapshots_dir / f"{match_id}.csv"
            clean_rows = read_csv(clean_snapshot_path)
            if len(clean_rows) >= args.min_snapshots:
                market_snaps = clean_rows
                reasons = []
                status = "analysis_ready"
                clean_source = True
        if market_snaps:
            with (snap_dir / f"{market_id}.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=SNAPSHOT_HEADERS)
                writer.writeheader()
                writer.writerows(market_snaps)

        team_a_win = ""
        if clean_source:
            team_a_win = existing_clean[match_id].get("yes_won", "")
        elif radiant_win is not None and mapping in {"normal", "reversed"}:
            team_a_win = int(bool(radiant_win) if mapping == "normal" else not bool(radiant_win))
        draft_ts, draft_source = draft_completed_ts(details.get(match_id))
        earliest_ts, earliest_mid = earliest_book(yes_books, no_books)
        team_a_id, team_b_id = team_ids(market, details.get(match_id))
        rows.append(
            {
                "market_id": market_id,
                "match_id": match_id,
                "condition_id": str(market.get("condition_id") or ""),
                "token_id_yes": str(market.get("yes_token_id") or ""),
                "token_id_no": str(market.get("no_token_id") or ""),
                "team_a": str(market.get("yes_team") or ""),
                "team_b": str(market.get("no_team") or ""),
                "team_a_id": team_a_id,
                "team_b_id": team_b_id,
                "team_a_is_radiant": int(mapping == "normal") if mapping in {"normal", "reversed"} else "",
                "tournament_name": tournament_name(market),
                "game_number": game_number(str(market.get("name") or "")),
                "team_a_win": team_a_win,
                "draft_completed_ts": draft_ts,
                "draft_completion_source": draft_source,
                "earliest_book_ts": earliest_ts,
                "earliest_book_mid": earliest_mid,
                "anchor_source": "existing_clean" if clean_source else "earliest_polymarket_book",
                "snapshots": len(market_snaps) if market_snaps else len(snaps),
                "has_book": int(market_book_presence(market, books)),
                "quality_status": status,
                "quality_reason": ",".join(reasons),
            }
        )

    with (out_dir / "matches.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "market_rows": len(rows),
        "analysis_ready": sum(r["quality_status"] == "analysis_ready" for r in rows),
        "diagnostic_only": sum(r["quality_status"] != "analysis_ready" for r in rows),
        "duplicate_match_id_market_rows": sum(match_id_counts[r["match_id"]] > 1 for r in rows),
        "quality_reasons": dict(Counter(r["quality_reason"] or "analysis_ready" for r in rows)),
        "draft_completion_sources": dict(Counter(r["draft_completion_source"] for r in rows)),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"wrote {out_dir / 'matches.csv'}")
    print(f"wrote {snap_dir}")


if __name__ == "__main__":
    main()
