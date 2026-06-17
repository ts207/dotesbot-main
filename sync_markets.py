from __future__ import annotations

import argparse
import asyncio
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from team_utils import norm_team, teams_match

import aiohttp
import yaml

from discover_markets import MARKETS_YAML, main as discover_main
from mapping import RUNTIME_MARKETS_PATH, load_mappings, apply_runtime_overlay
from mapping_audit import audit_mappings, quarantine_critical_issues
from steam_client import fetch_all_live_games


PLACEHOLDER_MATCH_ID = "STEAM_MATCH_OR_LOBBY_ID_HERE"



def match_direction(mapping: dict, game: dict) -> str | None:
    yes = mapping.get("yes_team")
    no = mapping.get("no_team")
    radiant = game.get("radiant_team")
    dire = game.get("dire_team")
    if teams_match(yes, radiant) and teams_match(no, dire):
        return "normal"
    if teams_match(yes, dire) and teams_match(no, radiant):
        return "reversed"
    return None


def game_number(mapping: dict) -> int:
    text = " ".join(str(mapping.get(k) or "") for k in ("name", "slug"))
    match = re.search(r"\bGame\s*(\d+)\b", text, flags=re.I)
    return int(match.group(1)) if match else 999


def is_placeholder_match_id(value: Any) -> bool:
    text = str(value or "")
    return not text or PLACEHOLDER_MATCH_ID in text


def is_active_mapping(mapping: dict) -> bool:
    try:
        confidence = float(mapping.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    return confidence >= 0.98 and not is_placeholder_match_id(mapping.get("dota_match_id"))


def live_match_id(game: dict) -> str:
    return str(game.get("match_id") or game.get("lobby_id") or "")


def _scheduled_start_dt(mapping: dict) -> Any:
    """Parse scheduled_start_utc to a datetime, or None if missing/unparseable."""
    raw = mapping.get("scheduled_start_utc")
    if not raw:
        return None
    s = str(raw).replace("Z", "+00:00")
    try:
        from datetime import datetime as _dt
        # Handle both 'YYYY-MM-DD HH:MM:SS+00:00' and ISO formats
        return _dt.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _is_market_in_window(mapping: dict, window_days: int = 2) -> bool:
    """True if the market's scheduled_start_utc is within ±window_days of now.
    Markets without a scheduled date are allowed (legacy / unscheduled)."""
    dt = _scheduled_start_dt(mapping)
    if dt is None:
        return True
    from datetime import datetime as _dt, timedelta, timezone as _tz
    now = _dt.now(_tz.utc)
    delta = abs((dt - now).total_seconds())
    return delta <= window_days * 86400


def choose_mapping_for_live_game(markets: list[dict], game: dict) -> tuple[dict | None, str]:
    matching = [m for m in markets if match_direction(m, game)]
    if not matching:
        return None, "no_team_match"

    existing_same_match = [
        m for m in matching
        if str(m.get("dota_match_id") or "") == live_match_id(game) and is_active_mapping(m)
    ]
    if existing_same_match:
        return None, "already_mapped_current_match"

    def _is_quarantined(mapping: dict) -> bool:
        if mapping.get("mapping_state") != "quarantined":
            return False
        q_until = mapping.get("quarantined_until")
        if not q_until:
            return True
        try:
            from datetime import datetime as _dt, timezone as _tz
            dt = _dt.fromisoformat(str(q_until).replace("Z", "+00:00"))
            return dt > _dt.now(_tz.utc)
        except Exception:
            return True

    candidates = [
        m for m in matching
        if not _is_quarantined(m) and (is_placeholder_match_id(m.get("dota_match_id")) or not is_active_mapping(m))
    ]
    if not candidates:
        return None, "no_inactive_candidate"

    # 2026-05-30 — Filter candidates to markets scheduled around now (±2 days).
    # Without this, the mapper happily binds today's Steam game to a closed
    # Polymarket market from weeks ago whose team names happen to match.
    # See conversation 2026-05-30: Liquid vs Aurora today bound to a May 13
    # closed market because no date check existed.
    fresh = [m for m in candidates if _is_market_in_window(m, window_days=2)]
    if not fresh:
        return None, "no_in_window_market"
    candidates = fresh

    # Activate only one map for a live Steam match. Re-running after the next
    # Steam match_id appears advances to the next Game N market.
    candidates.sort(key=game_number)
    return candidates[0], "matched"


def sync_markets_to_games(
    markets: list[dict],
    games: list[dict],
    *,
    only_pair: tuple[str, str] | None = None,
) -> list[dict]:
    updates: list[dict] = []
    used_market_ids: set[int] = set()

    named_games = [
        g for g in games
        if live_match_id(g) and (g.get("radiant_team") or g.get("dire_team"))
    ]

    # Pass 1: link MAP_WINNER markets (one game → one market, lowest game number wins).
    for game in named_games:
        if only_pair:
            game_pair = sorted([norm_team(game.get("radiant_team")), norm_team(game.get("dire_team"))])
            target_pair = sorted([norm_team(only_pair[0]), norm_team(only_pair[1])])
            if game_pair != target_pair:
                continue
        market, reason = choose_mapping_for_live_game(markets, game)
        if not market or id(market) in used_market_ids:
            continue

        direction = match_direction(market, game)
        market["dota_match_id"] = live_match_id(game)
        market["confidence"] = 1.0
        market["auto_mapped_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        market["auto_mapped_source"] = game.get("data_source")
        market["steam_radiant_team"] = game.get("radiant_team")
        market["steam_dire_team"] = game.get("dire_team")
        market["steam_side_mapping"] = direction

        used_market_ids.add(id(market))
        updates.append({
            "market_name": market.get("name"),
            "dota_match_id": market.get("dota_match_id"),
            "radiant_team": game.get("radiant_team"),
            "dire_team": game.get("dire_team"),
            "game_time_sec": game.get("game_time_sec"),
            "direction": direction,
        })

    # Pass 2: link MATCH_WINNER markets to the same live game as their MAP_WINNER
    # counterpart.  A MATCH_WINNER market covers the full series so it should track
    # whichever game is currently live.  We allow it to share a match_id with the
    # MAP_WINNER (no used_market_ids guard here).
    linked_game_ids = {
        str(m.get("dota_match_id"))
        for m in markets
        if str(m.get("market_type", "")).upper() == "MAP_WINNER" and is_active_mapping(m)
    }
    for game in named_games:
        gid = live_match_id(game)
        if gid not in linked_game_ids:
            continue  # no MAP_WINNER linked to this game yet
        for market in markets:
            if str(market.get("market_type", "")).upper() != "MATCH_WINNER":
                continue
            if is_active_mapping(market) and str(market.get("dota_match_id")) == gid:
                continue  # already linked to this exact game
            if not match_direction(market, game):
                continue  # different teams
            if is_active_mapping(market):
                continue  # already linked to a different game (series already done)

            # Determine the game number from the MAP_WINNER market that shares this game.
            linked_map = next(
                (m for m in markets
                 if str(m.get("market_type", "")).upper() == "MAP_WINNER"
                 and str(m.get("dota_match_id")) == gid
                 and match_direction(m, game)),
                None,
            )
            gnum = game_number(linked_map) if linked_map else 1

            direction = match_direction(market, game)
            market["dota_match_id"] = gid
            market["confidence"] = 1.0
            market["auto_mapped_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            market["auto_mapped_source"] = game.get("data_source")
            market["steam_radiant_team"] = game.get("radiant_team")
            market["steam_dire_team"] = game.get("dire_team")
            market["steam_side_mapping"] = direction
            market["current_game_number"] = gnum
            # G1 is always 0-0; G2 score depends on G1 result (unknown here — signal
            # engine uses series_score_yes/no for sensitivity, defaulting to 0-0).
            if gnum == 1 and market.get("series_score_yes") is None:
                market["series_score_yes"] = 0
                market["series_score_no"] = 0
            if not market.get("p_next_yes"):
                market["p_next_yes"] = 0.5

            updates.append({
                "market_name": market.get("name"),
                "dota_match_id": gid,
                "radiant_team": game.get("radiant_team"),
                "dire_team": game.get("dire_team"),
                "game_time_sec": game.get("game_time_sec"),
                "direction": direction,
            })
            break  # one MATCH_WINNER per game

    return updates


def _atomic_write_yaml(data: dict, path: str | Path) -> None:
    """Write YAML data atomically to a file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{p.name}.",
        suffix=".tmp",
        dir=str(p.parent),
        text=True,
    )
    tmp_path = Path(tmp_name)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, p)

        try:
            dir_fd = os.open(str(p.parent), os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except (AttributeError, OSError):
            pass

    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def load_markets(path: str | Path | None = None) -> dict:
    """Load markets from a specific file, merged with runtime overlay if using default path."""
    if path is None:
        path = MARKETS_YAML
    p = Path(path)
    base_markets = []
    if p.exists():
        with p.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            base_markets = data.get("markets", []) or []
    
    if str(path) == str(MARKETS_YAML):
        merged = apply_runtime_overlay(base_markets)
        return {"markets": merged}
    
    return {"markets": base_markets}


def write_markets(data: dict, path: str | Path | None = None) -> None:
    """Write markets to a file. Defaults to the runtime overlay file to avoid repo pollution."""
    if path is None:
        path = RUNTIME_MARKETS_PATH
    _atomic_write_yaml(data, path)


async def sync_once(
    *,
    discover: bool = True,
    write: bool = True,
    only_pair: tuple[str, str] | None = None,
) -> list[dict]:
    if discover:
        # 2026-06-17: Pass RUNTIME_MARKETS_PATH so discoveries don't pollute markets.yaml
        await discover_main(auto_write=True, output_path=RUNTIME_MARKETS_PATH)

    # Load MERGED state so we don't overwrite existing runtime mappings with defaults
    markets = load_mappings()
    data = {"markets": markets}

    async with aiohttp.ClientSession() as session:
        games = await fetch_all_live_games(session)

    updates = sync_markets_to_games(markets, games, only_pair=only_pair)
    games_by_match_id = {live_match_id(game): game for game in games if live_match_id(game)}
    audit_issues = audit_mappings(markets, games_by_match_id=games_by_match_id)
    quarantined = quarantine_critical_issues(markets, audit_issues)
    if quarantined:
        print(f"mapping_audit quarantined {quarantined} critical mapping(s)")
    if (updates or quarantined) and write:
        write_markets(data)
    return updates


async def watch(interval_seconds: float, *, discover: bool, only_pair: tuple[str, str] | None = None) -> None:
    while True:
        updates = await sync_once(discover=discover, write=True, only_pair=only_pair)
        if updates:
            for update in updates:
                print(
                    f"mapped {update['market_name']} -> {update['dota_match_id']} "
                    f"({update['radiant_team']} vs {update['dire_team']}, t={update['game_time_sec']})"
                )
        else:
            print("no new live market mappings")
        await asyncio.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-discover", action="store_true", help="Skip Polymarket discovery before Steam sync")
    parser.add_argument("--dry-run", action="store_true", help="Do not write Steam mappings")
    parser.add_argument("--watch", action="store_true", help="Keep syncing live Steam matches")
    parser.add_argument("--interval", type=float, default=30.0, help="Watch interval seconds")
    parser.add_argument("--teams", nargs=2, metavar=("TEAM_A", "TEAM_B"), help="Only sync this team pair")
    args = parser.parse_args()
    only_pair = tuple(args.teams) if args.teams else None

    if args.watch:
        asyncio.run(watch(args.interval, discover=not args.no_discover, only_pair=only_pair))
        return

    updates = asyncio.run(sync_once(discover=not args.no_discover, write=not args.dry_run, only_pair=only_pair))
    if updates:
        for update in updates:
            print(
                f"mapped {update['market_name']} -> {update['dota_match_id']} "
                f"({update['radiant_team']} vs {update['dire_team']}, t={update['game_time_sec']})"
            )
    else:
        print("no new live market mappings")


if __name__ == "__main__":
    main()
