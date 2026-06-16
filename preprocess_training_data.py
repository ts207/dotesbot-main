#!/usr/bin/env python3
"""
Two-pass preprocessing: liveleague_raw.jsonl → training CSV for dota_fair model.

Pass 1: scan all lines to determine match winners (from final tower_state).
Pass 2: extract feature rows and write CSV with radiant_win label.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

AEGIS_ITEM_ID = 36
JSONL_PATH = Path("logs/liveleague_raw.jsonl")
OUTPUT_CSV = Path("logs/training_data.csv")
TEAM_STATS_PATH = Path("dota_fair_model/models/team_stats.json")
MIN_GAME_TIME_SEC = 5 * 60  # discard very early-game snapshots
MIN_SNAPSHOTS_PER_MATCH = 20  # discard matches with too few observations


def _players(side_dict: dict) -> list[dict]:
    return side_dict.get("players") or []


def _f(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        result = float(v)
        return None if math.isnan(result) else result
    except (TypeError, ValueError):
        return None


def _sorted_nw(players: list[dict], n: int = 5) -> list[float]:
    values = sorted((_f(p.get("net_worth")) or 0.0 for p in players), reverse=True)
    while len(values) < n:
        values.append(0.0)
    return values[:n]


def extract_row(obj: dict, team_stats: dict) -> dict | None:
    raw = obj.get("raw")
    if not isinstance(raw, dict):
        return None
    sb = raw.get("scoreboard")
    if not isinstance(sb, dict):
        return None
    rad = sb.get("radiant") or {}
    dire = sb.get("dire") or {}
    rad_players = _players(rad)
    dire_players = _players(dire)
    if len(rad_players) < 5 or len(dire_players) < 5:
        return None

    gt = _f(obj.get("game_time_sec"))
    if gt is None or gt < MIN_GAME_TIME_SEC:
        return None

    def _team_stat(team_id) -> float:
        tid = str(team_id) if team_id else ""
        return float(team_stats.get(tid, 0.5))

    def _sum_field(players, key):
        vals = [_f(p.get(key)) for p in players]
        vals = [v for v in vals if v is not None]
        return sum(vals) if vals else None

    def _count_dead(players):
        return sum(1 for p in players if (_f(p.get("respawn_timer")) or 0) > 0)

    def _max_respawn(all_players):
        vals = [_f(p.get("respawn_timer")) or 0 for p in all_players]
        return max(vals) if vals else 0

    def _has_aegis(players):
        for p in players:
            for slot in range(6):
                if _f(p.get(f"item{slot}")) == AEGIS_ITEM_ID:
                    return 1.0
        return 0.0

    rad_nw_list = _sorted_nw(rad_players)
    dire_nw_list = _sorted_nw(dire_players)

    rad_score = _f(rad.get("score"))
    dire_score = _f(dire.get("score"))

    row = {
        "match_id": str(obj.get("match_id") or ""),
        "game_time_sec": gt,
        "radiant_score": rad_score,
        "dire_score": dire_score,
        "radiant_tower_state": _f(rad.get("tower_state")),
        "dire_tower_state": _f(dire.get("tower_state")),
        "radiant_barracks_state": _f(rad.get("barracks_state")),
        "dire_barracks_state": _f(dire.get("barracks_state")),
        "radiant_net_worth": _sum_field(rad_players, "net_worth"),
        "dire_net_worth": _sum_field(dire_players, "net_worth"),
        "radiant_gpm": _sum_field(rad_players, "gold_per_min"),
        "dire_gpm": _sum_field(dire_players, "gold_per_min"),
        "radiant_xpm": _sum_field(rad_players, "xp_per_min"),
        "dire_xpm": _sum_field(dire_players, "xp_per_min"),
        "radiant_gold": _sum_field(rad_players, "gold"),
        "dire_gold": _sum_field(dire_players, "gold"),
        "radiant_level": _sum_field(rad_players, "level"),
        "dire_level": _sum_field(dire_players, "level"),
        "radiant_dead_count": _count_dead(rad_players),
        "dire_dead_count": _count_dead(dire_players),
        "radiant_core_dead_count": _count_dead(rad_players[:3]),
        "dire_core_dead_count": _count_dead(dire_players[:3]),
        "max_respawn_timer": _max_respawn(rad_players + dire_players),
        "radiant_has_aegis": _has_aegis(rad_players),
        "dire_has_aegis": _has_aegis(dire_players),
        "radiant_team_win_ratio": _team_stat(obj.get("radiant_team")),
        "dire_team_win_ratio": _team_stat(obj.get("dire_team")),
    }
    for i, nw in enumerate(rad_nw_list, 1):
        row[f"radiant_p{i}_net_worth"] = nw
    for i, nw in enumerate(dire_nw_list, 1):
        row[f"dire_p{i}_net_worth"] = nw

    return row


def determine_winner(rad_tw: int | None, dire_tw: int | None,
                     rad_sc: float | None, dire_sc: float | None) -> int | None:
    """Return 1 if radiant won, 0 if dire won, None if ambiguous."""
    if rad_tw is not None and dire_tw is not None:
        if rad_tw == 0 and dire_tw > 0:
            return 0  # all radiant towers gone → dire won
        if dire_tw == 0 and rad_tw > 0:
            return 1  # all dire towers gone → radiant won
        if rad_tw < dire_tw:
            return 0  # radiant more damaged
        if dire_tw < rad_tw:
            return 1  # dire more damaged
    # Fallback: score comparison
    if rad_sc is not None and dire_sc is not None and rad_sc != dire_sc:
        return 1 if rad_sc > dire_sc else 0
    return None


def pass1_find_winners(jsonl_path: Path) -> dict[str, int]:
    """Find winner for each match from the last snapshot with valid game_time."""
    print("Pass 1: scanning for match winners...", flush=True)
    # match_id → (max_game_time, rad_tw, dire_tw, rad_sc, dire_sc)
    last: dict[str, tuple] = {}
    total = 0
    for line in open(jsonl_path):
        total += 1
        if total % 1_000_000 == 0:
            print(f"  {total:,} lines...", flush=True)
        try:
            obj = json.loads(line)
            gt = _f(obj.get("game_time_sec"))
            if gt is None:
                continue
            mid = str(obj.get("match_id") or "")
            if not mid:
                continue
            raw = obj.get("raw")
            if not isinstance(raw, dict):
                continue
            sb = raw.get("scoreboard") or {}
            rad = sb.get("radiant") or {}
            dire = sb.get("dire") or {}
            rad_tw = _f(rad.get("tower_state"))
            dire_tw = _f(dire.get("tower_state"))
            rad_sc = _f(rad.get("score"))
            dire_sc = _f(dire.get("score"))
            prev = last.get(mid)
            if prev is None or gt > prev[0]:
                last[mid] = (gt, rad_tw, dire_tw, rad_sc, dire_sc)
        except Exception:
            pass

    print(f"  {total:,} lines scanned, {len(last)} unique matches")

    winners: dict[str, int] = {}
    ambiguous = 0
    for mid, (gt, rad_tw, dire_tw, rad_sc, dire_sc) in last.items():
        result = determine_winner(
            int(rad_tw) if rad_tw is not None else None,
            int(dire_tw) if dire_tw is not None else None,
            rad_sc, dire_sc,
        )
        if result is not None:
            winners[mid] = result
        else:
            ambiguous += 1

    print(f"  Winners resolved: {len(winners)}, ambiguous: {ambiguous}")
    radiant_wins = sum(winners.values())
    print(f"  Radiant wins: {radiant_wins} ({radiant_wins/len(winners)*100:.1f}%)")
    return winners


def pass2_extract_features(
    jsonl_path: Path,
    winners: dict[str, int],
    team_stats: dict,
    output_csv: Path,
) -> None:
    print("\nPass 2: extracting features...", flush=True)

    from dota_fair_model.features import DEFAULT_FEATURE_COLUMNS

    fieldnames = ["match_id", "radiant_win"] + DEFAULT_FEATURE_COLUMNS + [
        f"radiant_p{i}_net_worth" for i in range(1, 6)
    ] + [f"dire_p{i}_net_worth" for i in range(1, 6)]

    # Count snapshots per match to filter low-count matches
    match_counts: dict[str, int] = {}
    written = 0
    skipped_no_winner = 0
    skipped_bad_row = 0

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()

        total = 0
        # Buffer rows per match temporarily to apply min_snapshots filter
        buffer: dict[str, list[dict]] = {}
        flushed: set[str] = set()

        def flush_match(mid: str):
            rows = buffer.pop(mid, [])
            if len(rows) < MIN_SNAPSHOTS_PER_MATCH:
                return 0
            for r in rows:
                writer.writerow(r)
            return len(rows)

        for line in open(jsonl_path):
            total += 1
            if total % 1_000_000 == 0:
                print(f"  {total:,} lines, {written:,} rows written...", flush=True)
            try:
                obj = json.loads(line)
                mid = str(obj.get("match_id") or "")
                if mid not in winners:
                    skipped_no_winner += 1
                    continue
                row = extract_row(obj, team_stats)
                if row is None:
                    skipped_bad_row += 1
                    continue
                row["radiant_win"] = winners[mid]
                if mid not in buffer:
                    buffer[mid] = []
                buffer[mid].append(row)
            except Exception:
                skipped_bad_row += 1

        # Flush remaining
        for mid in list(buffer.keys()):
            written += flush_match(mid)

    print(f"  {total:,} lines processed")
    print(f"  {written:,} feature rows written to {output_csv}")
    print(f"  Skipped (no winner): {skipped_no_winner:,}, bad rows: {skipped_bad_row:,}")


def main():
    print(f"Loading team stats from {TEAM_STATS_PATH}...")
    team_stats = json.loads(TEAM_STATS_PATH.read_text())
    print(f"  {len(team_stats)} teams loaded")

    winners = pass1_find_winners(JSONL_PATH)
    if not winners:
        print("ERROR: no winners found", file=sys.stderr)
        sys.exit(1)

    pass2_extract_features(JSONL_PATH, winners, team_stats, OUTPUT_CSV)
    print(f"\nDone. Training CSV: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
