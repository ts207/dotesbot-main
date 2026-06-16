"""GetRealtimeStats delayed rich-context parser.

GetTopLiveGame is the fast execution source for duration, score, building
state, and aggregate radiant net-worth lead. GetRealtimeStats is delayed, but
it contains richer player/detail fields. This module attaches those delayed
details without overwriting fast fields used for event detection and current
win-probability anchoring.
"""

import time
import logging
from typing import Any

from config import STEAM_API_KEY, REALTIME_STATS_ENABLED, REALTIME_STATS_STALE_SEC

try:
    import aiohttp
except ImportError:
    aiohttp = None

logger = logging.getLogger(__name__)

REALTIME_STATS_URL = "https://api.steampowered.com/IDOTA2MatchStats_570/GetRealtimeStats/v1/"
AEGIS_ITEM_ID = 117

_cache: dict[str, tuple[dict | None, float]] = {}


def _first_present(*values):
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _to_int(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _team_side(team: dict, fallback_idx: int | None = None) -> str | None:
    """Resolve a GetRealtimeStats team object to radiant/dire.

    Prefer explicit API fields when present. Fall back to Valve's common team
    numbers, then array order for older/minimal payloads.
    """
    if not isinstance(team, dict):
        return None
    for key in ("side", "team_side", "faction"):
        value = team.get(key)
        if isinstance(value, str):
            lowered = value.lower()
            if "radiant" in lowered:
                return "radiant"
            if "dire" in lowered:
                return "dire"
    for key in ("is_radiant", "radiant"):
        value = team.get(key)
        if isinstance(value, bool):
            return "radiant" if value else "dire"
    for key in ("team_number", "team_slot", "side_id"):
        value = _to_int(team.get(key))
        if value in (0, 2):
            return "radiant"
        if value in (1, 3):
            return "dire"
    if fallback_idx == 0:
        return "radiant"
    if fallback_idx == 1:
        return "dire"
    return None


async def _fetch_realtime_stats(session: Any, server_steam_id: str) -> tuple[dict | None, float]:
    if aiohttp is None or not STEAM_API_KEY:
        return None, 0.0
    try:
        async with session.get(
            REALTIME_STATS_URL,
            params={"key": STEAM_API_KEY, "server_steam_id": server_steam_id},
            timeout=aiohttp.ClientTimeout(total=5),
        ) as r:
            r.raise_for_status()
            raw = await r.read()
            import json
            data = json.loads(raw.decode("utf-8", errors="replace"))
            return data, time.time()
    except Exception as exc:
        logger.debug("GetRealtimeStats failed for %s: %s", server_steam_id, exc)
        return None, 0.0


def parse_player_net_worth(data: dict | None) -> dict[str, Any] | None:
    """Parse GetRealtimeStats response.

    Field names in the actual API response differ from what GetLiveLeagueGames used:
      hero_id → heroid, kills → kill_count, deaths → death_count,
      assists → assists_count, last_hits → lh_count, denies → denies_count.
    Items is a flat list of IDs (9 slots: 6 inventory + 3 backpack), not item0-item5 keys.
    Respawn timers are not present in this endpoint.
    game_time lives at result.match.game_time, not result.game_time.
    team side comes from team_number: 2=radiant, 3=dire.
    """
    if not data:
        return None
    result = data.get("result") or data
    teams = result.get("teams") or []
    if len(teams) < 2:
        return None

    # Side assignment via team_number (2=radiant, 3=dire), fallback to index order
    side_teams: dict[str, dict] = {}
    for idx, team in enumerate(teams):
        tn = _to_int(team.get("team_number"))
        if tn == 2:
            side = "radiant"
        elif tn == 3:
            side = "dire"
        else:
            side = "radiant" if idx == 0 else "dire"
        if side not in side_teams:
            side_teams[side] = team

    if "radiant" not in side_teams or "dire" not in side_teams:
        return None

    out: dict[str, Any] = {}
    radiant_nw = 0
    dire_nw = 0
    radiant_level = 0
    dire_level = 0
    aegis_team: str | None = None
    aegis_holder_hero_id: int | None = None

    for side_name in ("radiant", "dire"):
        team = side_teams[side_name]
        players = team.get("players") or []
        top_nw_values: list[int] = []

        for p_idx, player in enumerate(players):
            nw = _to_int(player.get("net_worth")) or 0
            level = _to_int(player.get("level")) or 0
            hero_id = _to_int(_first_present(player.get("heroid"), player.get("hero_id")))

            out[f"{side_name}_p{p_idx+1}_net_worth"] = nw
            out[f"{side_name}_p{p_idx+1}_hero_id"] = hero_id
            out[f"{side_name}_p{p_idx+1}_level"] = level
            out[f"{side_name}_p{p_idx+1}_kills"] = _to_int(_first_present(player.get("kill_count"), player.get("kills")))
            out[f"{side_name}_p{p_idx+1}_deaths"] = _to_int(_first_present(player.get("death_count"), player.get("death"), player.get("deaths")))
            out[f"{side_name}_p{p_idx+1}_assists"] = _to_int(_first_present(player.get("assists_count"), player.get("assists")))
            out[f"{side_name}_p{p_idx+1}_last_hits"] = _to_int(_first_present(player.get("lh_count"), player.get("last_hits")))
            out[f"{side_name}_p{p_idx+1}_denies"] = _to_int(_first_present(player.get("denies_count"), player.get("denies")))
            out[f"{side_name}_p{p_idx+1}_gold"] = _to_int(player.get("gold"))
            # gpm/xpm not provided by this endpoint
            out[f"{side_name}_p{p_idx+1}_gpm"] = None
            out[f"{side_name}_p{p_idx+1}_xpm"] = None
            # respawn_timer not provided by this endpoint
            out[f"{side_name}_p{p_idx+1}_respawn_timer"] = None

            # Items: flat list [inv0..inv5, bp0..bp2], -1 = empty slot
            items_list = player.get("items") or []
            item_ids_valid: list[int] = []
            for slot_idx, raw_id in enumerate(items_list[:9]):
                item_id = _to_int(raw_id)
                slot_name = f"item{slot_idx}" if slot_idx < 6 else f"backpack{slot_idx - 6}"
                out[f"{side_name}_p{p_idx+1}_{slot_name}"] = item_id if item_id and item_id > 0 else None
                if item_id and item_id > 0:
                    item_ids_valid.append(item_id)

            if aegis_team is None and AEGIS_ITEM_ID in item_ids_valid:
                aegis_team = side_name
                aegis_holder_hero_id = hero_id

            top_nw_values.append(nw)
            if side_name == "radiant":
                radiant_nw += nw
                radiant_level += level
            else:
                dire_nw += nw
                dire_level += level

        top3 = sorted(top_nw_values, reverse=True)[:3]
        out[f"{side_name}_top3_nw"] = sum(top3)
        out[f"realtime_{side_name}_team_name"] = team.get("team_name")
        out[f"realtime_{side_name}_team_id"] = _to_int(team.get("team_id"))
        out[f"{side_name}_score"] = _to_int(team.get("score"))

    # game_time is nested inside result.match.game_time
    match_obj = result.get("match") or {}
    delayed_game_time = _to_int(match_obj.get("game_time"))

    derived: list[str] = []
    if aegis_team == "radiant":
        derived.append("AEGIS_HELD_BY_RADIANT")
    elif aegis_team == "dire":
        derived.append("AEGIS_HELD_BY_DIRE")

    out.update({
        "realtime_game_time_sec": delayed_game_time,
        "delayed_game_time_sec": delayed_game_time,
        "realtime_radiant_nw": radiant_nw,
        "realtime_dire_nw": dire_nw,
        "realtime_lead_nw": radiant_nw - dire_nw,
        "delayed_radiant_net_worth": radiant_nw,
        "delayed_dire_net_worth": dire_nw,
        "delayed_net_worth_diff": radiant_nw - dire_nw,
        "radiant_level": radiant_level,
        "dire_level": dire_level,
        # Respawn/dead counts not available in GetRealtimeStats
        "radiant_dead_count": None,
        "dire_dead_count": None,
        "radiant_core_dead_count": None,
        "dire_core_dead_count": None,
        "radiant_max_respawn": None,
        "dire_max_respawn": None,
        "max_respawn_timer": None,
        "aegis_team": aegis_team,
        "aegis_holder_side": aegis_team,
        "aegis_holder_hero_id": aegis_holder_hero_id,
        "radiant_has_aegis": aegis_team == "radiant",
        "dire_has_aegis": aegis_team == "dire",
        "realtime_derived_events": derived,
    })
    return out


async def maybe_enrich_realtime(game: dict, session: Any = None) -> dict:
    if not REALTIME_STATS_ENABLED:
        return game
    server_steam_id = game.get("server_steam_id") or game.get("lobby_id")
    if not server_steam_id:
        return game

    now = time.time()
    cached = _cache.get(str(server_steam_id))
    if cached:
        cached_data, cached_time = cached
        if cached_data is not None and (now - cached_time) < REALTIME_STATS_STALE_SEC:
            parsed = parse_player_net_worth(cached_data)
            if parsed:
                game.update(parsed)
                game["realtime_stats_age_sec"] = round(now - cached_time, 2)
                game["delayed_field_age_sec"] = game["realtime_stats_age_sec"]
            return game

    if session is None:
        return game

    data, fetch_time = await _fetch_realtime_stats(session, str(server_steam_id))
    _cache[str(server_steam_id)] = (data, fetch_time or now)

    if data is not None:
        parsed = parse_player_net_worth(data)
        if parsed:
            game.update(parsed)
            game["realtime_stats_age_sec"] = 0.0
            game["delayed_field_age_sec"] = game["realtime_stats_age_sec"]

    return game


def clear_cache() -> None:
    _cache.clear()
