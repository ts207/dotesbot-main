#!/usr/bin/env python3
"""Build a Dota pro-game universe for network market mapping."""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


HEADERS = [
    "match_id",
    "game_id",
    "series_id",
    "start_ts",
    "league_id",
    "tournament_name",
    "radiant_team_id",
    "dire_team_id",
    "radiant_team_name",
    "dire_team_name",
    "radiant_win",
    "winner_team_id",
    "duration",
    "patch_epoch",
    "source",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def market_date_bounds(markets: list[dict[str, str]], margin_days: int) -> tuple[int | None, int | None]:
    dates = []
    for row in markets:
        if row.get("source_universe") == "local_clean_v2":
            continue
        dt = parse_ts(row.get("start_ts") or row.get("end_ts") or row.get("closed_ts"))
        if dt:
            dates.append(dt)
    if not dates:
        return None, None
    start = min(dates) - timedelta(days=margin_days)
    end = max(dates) + timedelta(days=margin_days)
    return int(start.timestamp()), int(end.timestamp())


def fetch_json(url: str, params: dict[str, Any] | None = None) -> Any:
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def load_details(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def detail_row(match_id: str, match: dict, source: str) -> dict[str, str]:
    radiant_id = str(match.get("radiant_team_id") or "")
    dire_id = str(match.get("dire_team_id") or "")
    radiant_win = match.get("radiant_win")
    winner = radiant_id if radiant_win is True else dire_id if radiant_win is False else ""
    return {
        "match_id": match_id,
        "game_id": match_id,
        "series_id": match_id,
        "start_ts": str(match.get("start_time") or ""),
        "league_id": str(match.get("leagueid") or match.get("league_id") or ""),
        "tournament_name": str(match.get("league_name") or ""),
        "radiant_team_id": radiant_id,
        "dire_team_id": dire_id,
        "radiant_team_name": str(match.get("radiant_name") or ""),
        "dire_team_name": str(match.get("dire_name") or ""),
        "radiant_win": "" if radiant_win is None else str(int(bool(radiant_win))),
        "winner_team_id": winner,
        "duration": str(match.get("duration") or ""),
        "patch_epoch": str(match.get("patch") or ""),
        "source": source,
    }


def promatch_row(match: dict) -> dict[str, str]:
    match_id = str(match.get("match_id") or "")
    radiant_win = match.get("radiant_win")
    radiant_id = str(match.get("radiant_team_id") or "")
    dire_id = str(match.get("dire_team_id") or "")
    winner = radiant_id if radiant_win is True else dire_id if radiant_win is False else ""
    return {
        "match_id": match_id,
        "game_id": match_id,
        "series_id": match_id,
        "start_ts": str(match.get("start_time") or ""),
        "league_id": str(match.get("leagueid") or ""),
        "tournament_name": str(match.get("league_name") or ""),
        "radiant_team_id": radiant_id,
        "dire_team_id": dire_id,
        "radiant_team_name": str(match.get("radiant_name") or ""),
        "dire_team_name": str(match.get("dire_name") or ""),
        "radiant_win": "" if radiant_win is None else str(int(bool(radiant_win))),
        "winner_team_id": winner,
        "duration": str(match.get("duration") or ""),
        "patch_epoch": "",
        "source": "opendota_proMatches",
    }


def load_cached_promatches(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_cached_promatches(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")


def fetch_promatches(start_ts: int | None, end_ts: int | None, args: argparse.Namespace) -> list[dict]:
    cached = load_cached_promatches(Path(args.raw_output))
    if cached and not args.refresh:
        return cached
    rows = []
    less_than = None
    for page in range(args.max_pages):
        params = {}
        if less_than:
            params["less_than_match_id"] = less_than
        page_rows = fetch_json("https://api.opendota.com/api/proMatches", params)
        if not isinstance(page_rows, list) or not page_rows:
            break
        rows.extend(page_rows)
        less_than = min(int(r["match_id"]) for r in page_rows if r.get("match_id"))
        oldest = min(int(r.get("start_time") or 0) for r in page_rows)
        newest = max(int(r.get("start_time") or 0) for r in page_rows)
        if page % 10 == 0:
            print(f"promatches_page={page + 1} rows={len(rows)} newest={newest} oldest={oldest}")
        if start_ts and oldest and oldest < start_ts:
            break
        time.sleep(args.sleep_sec)
    write_cached_promatches(Path(args.raw_output), rows)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-universe", default="data/processed/polymarket/dota_market_universe.csv")
    parser.add_argument("--details", default="logs/opendota_player_match_details.json")
    parser.add_argument("--raw-output", default="data/raw/opendota/pro_matches.jsonl")
    parser.add_argument("--output", default="data/processed/dota_game_universe.csv")
    parser.add_argument("--margin-days", type=int, default=7)
    parser.add_argument("--max-pages", type=int, default=250)
    parser.add_argument("--sleep-sec", type=float, default=1.05)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    markets = read_csv(Path(args.market_universe))
    start_ts, end_ts = market_date_bounds(markets, args.margin_days)
    details = load_details(Path(args.details))
    by_match = {mid: detail_row(mid, match, "opendota_match_detail_cache") for mid, match in details.items()}
    promatches = fetch_promatches(start_ts, end_ts, args)
    for match in promatches:
        row = promatch_row(match)
        ts = int(row["start_ts"] or 0)
        if start_ts and end_ts and not (start_ts <= ts <= end_ts):
            continue
        if row["match_id"]:
            by_match.setdefault(row["match_id"], row)
    rows = sorted(by_match.values(), key=lambda r: int(r.get("start_ts") or 0))
    write_csv(Path(args.output), rows)
    print(json.dumps({"dota_game_universe_rows": len(rows), "start_ts": start_ts, "end_ts": end_ts}, indent=2, sort_keys=True))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
