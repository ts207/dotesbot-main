#!/usr/bin/env python3
"""Fetch/export missing Dota outcomes, drafts, and player detail rows."""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.request
from pathlib import Path


GAME_HEADERS = [
    "game_id",
    "match_id",
    "series_id",
    "start_ts",
    "duration_seconds",
    "patch_epoch",
    "league_id",
    "tournament_name",
    "tournament_tier",
    "radiant_team_id",
    "dire_team_id",
    "radiant_team_name",
    "dire_team_name",
    "radiant_win",
    "winner_team_id",
]

DRAFT_HEADERS = ["game_id", "match_id", "radiant_hero_ids_json", "dire_hero_ids_json", "picks_bans_json", "role_inference_confidence"]

PLAYER_HEADERS = [
    "game_id",
    "match_id",
    "hero_id",
    "player_slot",
    "is_radiant",
    "lane_role",
    "lane",
    "kills",
    "assists",
    "deaths",
    "hero_damage",
    "tower_damage",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def fetch_match(match_id: str) -> dict:
    req = urllib.request.Request(f"https://api.opendota.com/api/matches/{match_id}", headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)


def slim_match(match_id: str, data: dict) -> dict:
    players = data.get("players") or []
    return {
        "match_id": match_id,
        "radiant_win": data.get("radiant_win"),
        "duration": data.get("duration"),
        "patch": data.get("patch"),
        "start_time": data.get("start_time"),
        "leagueid": data.get("leagueid"),
        "radiant_team_id": data.get("radiant_team_id"),
        "dire_team_id": data.get("dire_team_id"),
        "radiant_name": data.get("radiant_name"),
        "dire_name": data.get("dire_name"),
        "picks_bans": data.get("picks_bans"),
        "draft_timings": data.get("draft_timings"),
        "players": [
            {
                k: p.get(k)
                for k in [
                    "hero_id",
                    "player_slot",
                    "isRadiant",
                    "lane",
                    "lane_role",
                    "is_roaming",
                    "lane_efficiency",
                    "gold_t",
                    "xp_t",
                    "lh_t",
                    "kills",
                    "assists",
                    "deaths",
                    "hero_damage",
                    "tower_damage",
                    "teamfight_participation",
                ]
            }
            for p in players
        ],
    }


def write_csv(path: Path, rows: list[dict], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def export_processed(details: dict[str, dict], output_dir: Path) -> None:
    games = []
    drafts = []
    players = []
    for match_id, match in details.items():
        radiant_id = str(match.get("radiant_team_id") or "")
        dire_id = str(match.get("dire_team_id") or "")
        radiant_win = match.get("radiant_win")
        winner = radiant_id if radiant_win is True else dire_id if radiant_win is False else ""
        games.append(
            {
                "game_id": match_id,
                "match_id": match_id,
                "series_id": match_id,
                "start_ts": match.get("start_time") or "",
                "duration_seconds": match.get("duration") or "",
                "patch_epoch": match.get("patch") or "",
                "league_id": match.get("leagueid") or "",
                "tournament_name": "",
                "tournament_tier": "",
                "radiant_team_id": radiant_id,
                "dire_team_id": dire_id,
                "radiant_team_name": match.get("radiant_name") or "",
                "dire_team_name": match.get("dire_name") or "",
                "radiant_win": "" if radiant_win is None else int(bool(radiant_win)),
                "winner_team_id": winner,
            }
        )
        radiant_heroes = [p.get("hero_id") for p in match.get("players", []) if p.get("player_slot") is not None and int(p.get("player_slot")) < 128]
        dire_heroes = [p.get("hero_id") for p in match.get("players", []) if p.get("player_slot") is not None and int(p.get("player_slot")) >= 128]
        drafts.append(
            {
                "game_id": match_id,
                "match_id": match_id,
                "radiant_hero_ids_json": json.dumps(radiant_heroes, separators=(",", ":")),
                "dire_hero_ids_json": json.dumps(dire_heroes, separators=(",", ":")),
                "picks_bans_json": json.dumps(match.get("picks_bans") or [], separators=(",", ":")),
                "role_inference_confidence": "0.50",
            }
        )
        for p in match.get("players", []) or []:
            players.append(
                {
                    "game_id": match_id,
                    "match_id": match_id,
                    "hero_id": p.get("hero_id") or "",
                    "player_slot": p.get("player_slot") or "",
                    "is_radiant": int(int(p.get("player_slot")) < 128) if p.get("player_slot") is not None else "",
                    "lane_role": p.get("lane_role") or "",
                    "lane": p.get("lane") or "",
                    "kills": p.get("kills") or "",
                    "assists": p.get("assists") or "",
                    "deaths": p.get("deaths") or "",
                    "hero_damage": p.get("hero_damage") or "",
                    "tower_damage": p.get("tower_damage") or "",
                }
            )
    write_csv(output_dir / "dota_games.csv", games, GAME_HEADERS)
    write_csv(output_dir / "dota_drafts.csv", drafts, DRAFT_HEADERS)
    write_csv(output_dir / "player_match_rows.csv", players, PLAYER_HEADERS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-map", default="data/processed/market_game_map.csv")
    parser.add_argument("--outcomes", default="logs/opendota_outcomes.json")
    parser.add_argument("--details", default="logs/opendota_player_match_details.json")
    parser.add_argument("--output-dir", default="data/processed")
    parser.add_argument("--sleep-sec", type=float, default=1.05)
    args = parser.parse_args()

    mapped_ids = {r.get("match_id", "") for r in read_csv(Path(args.market_map)) if r.get("match_id")}
    outcomes = json.loads(Path(args.outcomes).read_text(encoding="utf-8")) if Path(args.outcomes).exists() else {}
    details = json.loads(Path(args.details).read_text(encoding="utf-8")) if Path(args.details).exists() else {}
    missing = sorted(mid for mid in mapped_ids if mid not in details)
    errors = []
    for idx, match_id in enumerate(missing, start=1):
        try:
            data = fetch_match(match_id)
            details[match_id] = slim_match(match_id, data)
            if data.get("radiant_win") is not None:
                outcomes[match_id] = bool(data.get("radiant_win"))
        except Exception as exc:
            errors.append({"match_id": match_id, "error": type(exc).__name__, "detail": str(exc)[:200]})
        if idx % 10 == 0 or idx == len(missing):
            Path(args.details).write_text(json.dumps(details, separators=(",", ":")), encoding="utf-8")
            Path(args.outcomes).write_text(json.dumps(outcomes, indent=2, sort_keys=True), encoding="utf-8")
        time.sleep(args.sleep_sec)
    export_processed(details, Path(args.output_dir))
    Path(args.output_dir, "dota_fetch_errors.json").write_text(json.dumps(errors, indent=2), encoding="utf-8")
    print(f"details={len(details)} outcomes={len(outcomes)} errors={len(errors)}")
    print(f"wrote {args.output_dir}")


if __name__ == "__main__":
    main()
