#!/usr/bin/env python3
"""Discover and normalize historical Polymarket Dota markets."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from discover_markets import (  # noqa: E402
    _is_map_winner_market,
    _outcome_token_pairs,
    _parse_teams,
)


GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
GAMMA_SEARCH_URL = "https://gamma-api.polymarket.com/public-search"


HEADERS = [
    "market_id",
    "condition_id",
    "event_id",
    "slug",
    "question",
    "description",
    "yes_token_id",
    "no_token_id",
    "outcomes_json",
    "resolved_outcome",
    "volume",
    "liquidity",
    "start_ts",
    "end_ts",
    "closed_ts",
    "candidate_team_a",
    "candidate_team_b",
    "is_locked_execution_audit",
    "market_discovery_source",
    "discovery_query",
    "source_universe",
    "market_team_a_raw",
    "market_team_b_raw",
    "market_team_a_norm",
    "market_team_b_norm",
    "game_number",
    "market_scope",
    "parsed_series_scope",
    "tournament_hint",
    "event_date_hint",
    "market_date_source",
    "market_date_confidence",
    "outcome_prices_json",
]


DEFAULT_SEARCH_QUERIES = [
    "dota",
    "dota 2",
    "esports dota",
    "Elite League Dota",
    "FISSURE Dota",
    "FISSURE Universe Dota",
    "BLAST Slam Dota",
    "Wallachia Dota",
    "Clavision Dota",
    "Clavision Masters Dota",
    "1win Series Dota",
    "European Pro League Dota",
    "CCT Dota",
    "Bali Major Dota",
    "Lima Major Dota",
    "Berlin Major Dota",
    "DPC Dota",
    "Western Europe DPC Dota",
    "Eastern Europe DPC Dota",
    "China DPC Dota",
    "Southeast Asia DPC Dota",
    "North America DPC Dota",
    "South America DPC Dota",
    "ESL Pro Tour Dota",
    "The International Dota",
    "The International Qualifier Dota",
    "TI Dota",
    "TI Qualifier Dota",
    "ESL One Dota",
    "ESL One Kuala Lumpur Dota",
    "ESL One Birmingham Dota",
    "ESL One Birmingham Qualifier Dota",
    "DreamLeague Dota",
    "DreamLeague Season Dota",
    "DreamLeague Western Europe Closed Qualifier Dota",
    "DreamLeague Eastern Europe Closed Qualifier Dota",
    "PGL Dota",
    "PGL Wallachia Dota",
    "BetBoom Dota",
    "BetBoom Dacha Dota",
    "Riyadh Masters Dota",
    "Team Spirit Dota",
    "Team Liquid Dota",
    "Gaimin Gladiators Dota",
    "BetBoom Team Dota",
    "1win Dota",
    "GamerLegion Dota",
    "Xtreme Gaming Dota",
    "Tundra Dota",
    "Falcons Dota",
    "PARIVISION Dota",
    "Aurora Dota",
    "Azure Ray Dota",
    "LGD Dota",
    "Shopify Rebellion Dota",
    "Nouns Dota",
    "MOUZ Dota",
    "Nemiga Dota",
    "L1GA Dota",
    "Nigma Galaxy Dota",
    "Team Secret Dota",
    "OG Dota",
    "Entity Dota",
    "Talon Dota",
    "BOOM Dota",
    "Execration Dota",
    "HEROIC Dota",
    "Vici Gaming Dota",
    "Yellow Submarine Dota",
    "Zero Tenacity Dota",
    "VP.Prodigy Dota",
    "Power Rangers Dota",
    "MODUS Dota",
    "South America Rejects Dota",
    "Team Yandex Dota",
    "PlayTime Dota",
    "Dota 2 Game Winner",
    "Dota 2 Game 1 Winner",
    "Dota 2 Game 2 Winner",
    "Dota 2 Game 3 Winner",
    "Dota 2 Map Winner",
    "Dota 2 Map 1 Winner",
    "Dota 2 Map 2 Winner",
    "Dota 2 Map 3 Winner",
    "Dota 2 Winner Game",
    "Dota 2 Winner Map",
    "Dota 2 Qualifier",
    "Dota 2 Closed Qualifier",
    "Dota 2 Open Qualifier",
    "Dota 2 Western Europe",
    "Dota 2 Eastern Europe",
    "Dota 2 Southeast Asia",
    "Dota 2 South America",
    "Dota 2 North America",
    "Dota 2 China",
    "Dota 2 MENA",
    "Dota 2 EEU",
    "Dota 2 WEU",
    "Dota 2 SEA",
    "Dota 2 SA",
    "Dota 2 NA",
    "Dota 2 CN",
    "FISSURE Universe Episode Dota",
    "FISSURE Universe Episode 4 Dota",
    "FISSURE Universe Episode 5 Dota",
    "PGL Wallachia Season Dota",
    "PGL Wallachia Season 4 Dota",
    "PGL Wallachia Season 5 Dota",
    "PGL Wallachia Season 6 Dota",
    "BLAST Slam II Dota",
    "BLAST Slam III Dota",
    "BLAST Slam IV Dota",
    "BLAST Slam V Dota",
    "DreamLeague Season 25 Dota",
    "DreamLeague Season 26 Dota",
    "DreamLeague Season 27 Dota",
    "DreamLeague Season 28 Dota",
    "DreamLeague Season 28 Qualifier Dota",
    "ESL One Raleigh Dota",
    "ESL One Bangkok Dota",
    "ESL One Europe Dota",
    "ESL One Asia Dota",
    "ESL One North America Dota",
    "ESL One South America Dota",
    "The International 2025 Dota",
    "The International 2025 Qualifier Dota",
    "The International 2026 Dota",
    "The International 2026 Qualifier Dota",
    "Riyadh Masters 2024 Dota",
    "Riyadh Masters 2025 Dota",
    "Riyadh Masters 2026 Dota",
    "EWC Dota",
    "Esports World Cup Dota",
    "Elite League Season 2 Dota",
    "Elite League Season 3 Dota",
    "Elite League Season 4 Dota",
    "Clavision Masters Snow Ruyi Dota",
    "Clavision Masters Dota 2",
    "Asian Champions League Dota",
    "ACL Dota",
    "MESA Dota",
    "RES Regional Dota",
    "Winline Dota",
    "BetBoom Streamers Battle Dota",
    "BetBoom Streamers Battle 8 Dota",
    "BetBoom Streamers Battle 9 Dota",
    "BetBoom Streamers Battle 10 Dota",
    "EPL Dota",
    "European Pro League Season Dota",
    "CCT Series Dota",
    "CCT Season Dota",
    "Dota 2 EPL",
    "Dota 2 CCT",
    "Dota 2 L1GA",
    "Dota 2 RES",
    "Dota 2 MESA",
    "Dota 2 FISSURE",
    "Dota 2 PGL",
    "Dota 2 BLAST",
    "Dota 2 DreamLeague",
    "Dota 2 ESL",
    "All Gamers Dota",
    "BOOM Esports Dota",
    "BOOM Esports Dota 2",
    "BOOM Esports vs Dota",
    "Talon Esports Dota",
    "Talon Esports Dota 2",
    "Talon vs Dota",
    "Entity Gaming Dota",
    "Entity Dota 2",
    "Team Secret Dota 2",
    "Natus Vincere Dota",
    "NAVI Dota",
    "Virtus.pro Dota",
    "Virtus Pro Dota",
    "Yakult Brothers Dota",
    "REKONIX Dota",
    "Amaru Flame Dota",
    "Amaru Gaming Dota",
    "Cheeki Breeki Dota",
    "Cheeki_Breeki Dota",
    "Noir Esports Dota",
    "SoloTeam Dota",
    "Travoman Team Dota",
    "Shizageddon Dota",
    "Nande+4 Dota",
    "Pipsqueak+4 Dota",
    "Team Nemesis Dota",
    "Summer Bear Dota",
    "Game 1 Winner Dota",
    "Game 2 Winner Dota",
    "Game 3 Winner Dota",
    "Map 1 Winner Dota",
    "Map 2 Winner Dota",
    "Map 3 Winner Dota",
    "Dota Game 1",
    "Dota Game 2",
    "Dota Game 3",
    "Dota Map 1",
    "Dota Map 2",
    "Dota Map 3",
    "Dota BO3 Game 1",
    "Dota BO3 Game 2",
    "Dota BO3 Game 3",
    "Dota 2 BO3 Game 1",
    "Dota 2 BO3 Game 2",
    "Dota 2 BO3 Game 3",
    "Dota 2 match scheduled",
    "Dota 2 this match",
    "Dota 2 Game Winner scheduled",
    "Dota 2 Map Winner scheduled",
    "Dota 2 EWC",
    "Dota 2 Esports World Cup",
    "Esports World Cup Qualifier Dota",
    "Esports World Cup Eastern Europe Dota",
    "Esports World Cup Western Europe Dota",
    "Esports World Cup Southeast Asia Dota",
    "Esports World Cup South America Dota",
    "Esports World Cup China Dota",
    "Esports World Cup MENA Dota",
    "CyberScore Dota",
    "Winline Insight Dota",
    "Winline Insight Season Dota",
    "CIS Battle Dota",
    "BTS Pro Series Dota",
    "MESA Nomadic Masters Dota",
    "MESA Invitational Dota",
    "Rivalry Dota",
    "Dota 2 Streamers Battle",
    "Streamers Battle Dota",
    "BetBoom Streamers Dota",
    "Team TPaBoMaH Dota",
    "Team 9pasha Dota",
    "Miposhka Team Dota",
    "Team Miposhka Dota",
    "by Owl Team Dota",
    "Team By_Owl Dota",
    "Game Master Dota",
    "Team Refuser Dota",
    "Yakult Brothers.Tearlaments Dota",
    "Yakult Brothers Tearlaments Dota",
    "Yakutou Brothers Dota",
    "Roar Gaming Dota",
    "Veroja Dota",
    "Veroja Gaming Dota",
    "Ivory Dota",
    "yache123 Dota",
    "1000 reasons Dota",
    "Golden Barys Dota",
    "Team Epoch Dota",
    "Stormrage Dota",
    "Valinor Dota",
    "Noir Dota",
    "Noir Esports Dota 2",
    "Shizageddon Dota 2",
    "SoloTeam Dota 2",
    "Travoman Team Dota 2",
    "Nande+4 Dota 2",
    "Cheeki_Breeki Dota 2",
    "Game Master vs Dota",
    "Yakult Brothers vs Dota",
    "Veroja vs Dota",
    "Ivory vs Dota",
    "Stormrage vs Dota",
    "Valinor vs Dota",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_ledger(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, separators=(",", ":"), sort_keys=True) + "\n")


def ledger_entry(
    *,
    endpoint: str,
    params: dict[str, Any],
    status: str,
    http_status: int | None,
    records_returned: int,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "script": Path(__file__).name,
        "request_url_or_endpoint": endpoint,
        "params": params,
        "status": status,
        "http_status": http_status,
        "fetched_at": utc_now(),
        "records_returned": records_returned,
        "error": error,
    }


def parse_token_ids(market: dict[str, Any]) -> tuple[str, str]:
    pairs = _outcome_token_pairs(market)
    if len(pairs) >= 2:
        return str(pairs[0][1]), str(pairs[1][1])
    token_ids = market.get("clobTokenIds") or market.get("clob_token_ids")
    if isinstance(token_ids, str):
        try:
            token_ids = json.loads(token_ids)
        except Exception:
            token_ids = []
    if isinstance(token_ids, list) and len(token_ids) >= 2:
        return str(token_ids[0]), str(token_ids[1])
    return "", ""


def outcomes_json(market: dict[str, Any]) -> str:
    outcomes = market.get("outcomes") or market.get("outcomePrices")
    if isinstance(outcomes, str):
        return outcomes
    return json.dumps(outcomes or [], separators=(",", ":"))


def outcome_prices_json(market: dict[str, Any]) -> str:
    prices = market.get("outcomePrices")
    if isinstance(prices, str):
        return prices
    return json.dumps(prices or [], separators=(",", ":"))


def parse_json_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return []
    if not isinstance(value, list):
        return []
    return [str(x) for x in value]


def infer_resolved_outcome(market: dict[str, Any]) -> str:
    explicit = str(market.get("resolution") or market.get("resolvedOutcome") or market.get("outcome") or "")
    if explicit:
        return explicit
    outcomes = parse_json_list(market.get("outcomes"))
    prices = parse_json_list(market.get("outcomePrices"))
    if len(outcomes) != len(prices):
        return ""
    for outcome, price in zip(outcomes, prices):
        try:
            if float(price) >= 0.99:
                return outcome
        except ValueError:
            continue
    return ""


def norm_name(text: str | None) -> str:
    text = (text or "").casefold()
    text = re.sub(r"\b(esports|gaming|team|club|clan)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def parse_market_title(question: str, description: str = "", slug: str = "") -> dict[str, str]:
    text = question.strip()
    clean = re.sub(r"^Dota\s*2:\s*", "", text, flags=re.I)
    parsed_series_scope = ""
    market_scope = "ambiguous"
    game_number = ""

    if re.search(r"\b(handicap|kills?|roshan|duration|total|map handicap)\b", text, flags=re.I):
        market_scope = "props"
    elif re.search(r"\btournament\b|\bwin Dota 2:\b", text, flags=re.I):
        market_scope = "tournament_winner"
    elif re.search(r"\(BO\d+\)", text, flags=re.I):
        market_scope = "series_winner"
        parsed_series_scope = re.search(r"\(BO(\d+)\)", text, flags=re.I).group(1)
    game_match = re.search(r"\b(?:Game|Map)\s*(\d+)\b", text, flags=re.I)
    if game_match:
        game_number = game_match.group(1)
        market_scope = "game_winner"

    team_text = re.sub(r"\s*-\s*(?:Game|Map)\s*\d+\s*Winner\s*$", "", clean, flags=re.I)
    team_text = re.sub(r"\s*\(BO\d+\)\s*$", "", team_text, flags=re.I)
    team_a, team_b = _parse_teams(team_text)
    if team_a == "Team A" and team_b == "Team B":
        team_a = team_b = ""

    tournament_hint = ""
    desc_match = re.search(r"in the ([^,\n]+)", description or "", flags=re.I)
    if desc_match:
        tournament_hint = desc_match.group(1).strip()
    event_date_hint = ""
    date_match = re.search(r"(20\d{2}-\d{2}-\d{2})", slug or "")
    if date_match:
        event_date_hint = date_match.group(1)
    return {
        "market_team_a_raw": team_a,
        "market_team_b_raw": team_b,
        "market_team_a_norm": norm_name(team_a),
        "market_team_b_norm": norm_name(team_b),
        "game_number": game_number,
        "market_scope": market_scope,
        "parsed_series_scope": parsed_series_scope,
        "tournament_hint": tournament_hint,
        "event_date_hint": event_date_hint,
    }


def choose_market_date_fields(parsed: dict[str, str], market: dict[str, Any]) -> dict[str, str]:
    if parsed.get("event_date_hint"):
        return {"market_date_source": "event_date_hint", "market_date_confidence": "0.95"}
    if market.get("endDate") or market.get("end_ts"):
        return {"market_date_source": "end_ts", "market_date_confidence": "0.85"}
    if market.get("startDate") or market.get("gameStartTime") or market.get("start_ts"):
        return {"market_date_source": "start_ts", "market_date_confidence": "0.75"}
    if market.get("closedTime") or market.get("closed_ts"):
        return {"market_date_source": "closed_ts", "market_date_confidence": "0.50"}
    return {"market_date_source": "", "market_date_confidence": "0.00"}


def is_club_exhibition_market(parsed: dict[str, str]) -> bool:
    teams = [parsed.get("market_team_a_raw", ""), parsed.get("market_team_b_raw", "")]
    return any(re.search(r"\bclub\b", team, flags=re.I) for team in teams)


def is_dota_market(market: dict[str, Any]) -> bool:
    text = " ".join(
        str(market.get(k) or "")
        for k in ("question", "title", "slug", "description", "category", "tags")
    ).lower()
    return "dota" in text or "dota 2" in text


def event_id_value(market: dict[str, Any]) -> str:
    event = market.get("event") or ""
    if isinstance(event, dict):
        event = event.get("id") or event.get("eventId") or ""
    return str(market.get("eventId") or market.get("event_id") or event or "")


def normalize_market(
    market: dict[str, Any],
    source: str,
    source_universe: str,
    locked_ids: set[str],
    discovery_query: str = "",
) -> dict[str, str] | None:
    if not is_dota_market(market):
        return None
    if not _is_map_winner_market(market):
        return None
    market_id = str(market.get("id") or market.get("marketId") or market.get("market_id") or "")
    condition_id = str(market.get("conditionId") or market.get("condition_id") or "")
    question = str(market.get("question") or market.get("title") or "")
    yes_token_id, no_token_id = parse_token_ids(market)
    if not market_id or not condition_id or not yes_token_id or not no_token_id:
        return None
    pairs = _outcome_token_pairs(market)
    if len(pairs) >= 2:
        team_a, team_b = pairs[0][0], pairs[1][0]
    else:
        team_a, team_b = _parse_teams(question)
    parsed = parse_market_title(question, str(market.get("description") or ""), str(market.get("slug") or ""))
    if is_club_exhibition_market(parsed):
        return None
    date_fields = choose_market_date_fields(parsed, market)
    return {
        "market_id": market_id,
        "condition_id": condition_id,
        "event_id": event_id_value(market),
        "slug": str(market.get("slug") or ""),
        "question": question,
        "description": str(market.get("description") or ""),
        "yes_token_id": yes_token_id,
        "no_token_id": no_token_id,
        "outcomes_json": outcomes_json(market),
        "resolved_outcome": infer_resolved_outcome(market),
        "volume": str(market.get("volume") or market.get("volumeNum") or ""),
        "liquidity": str(market.get("liquidity") or market.get("liquidityNum") or ""),
        "start_ts": str(market.get("startDate") or market.get("gameStartTime") or market.get("start_ts") or ""),
        "end_ts": str(market.get("endDate") or market.get("end_ts") or ""),
        "closed_ts": str(market.get("closedTime") or market.get("closed_ts") or ""),
        "candidate_team_a": str(team_a),
        "candidate_team_b": str(team_b),
        "is_locked_execution_audit": str(market_id in locked_ids),
        "market_discovery_source": source,
        "discovery_query": discovery_query,
        "source_universe": source_universe,
        **parsed,
        **date_fields,
        "outcome_prices_json": outcome_prices_json(market),
    }


async def fetch_json(session: aiohttp.ClientSession, url: str, params: dict[str, Any], ledger_path: Path) -> Any:
    headers = {"Accept-Encoding": "gzip, deflate", "User-Agent": "curl/8"}
    http_status = None
    try:
        async with session.get(url, params=params, headers=headers, timeout=30) as resp:
            http_status = resp.status
            if resp.status in {429, 520, 521, 522, 523, 524}:
                text = await resp.text()
                append_ledger(
                    ledger_path,
                    ledger_entry(
                        endpoint=url,
                        params=params,
                        status="rate_limited_or_throttled",
                        http_status=http_status,
                        records_returned=0,
                        error=text[:300],
                    ),
                )
                return []
            resp.raise_for_status()
            data = await resp.json()
            count = len(data) if isinstance(data, list) else sum(len(data.get(k) or []) for k in ("markets", "events", "results", "data")) if isinstance(data, dict) else 0
            append_ledger(
                ledger_path,
                ledger_entry(endpoint=url, params=params, status="ok" if count else "empty", http_status=http_status, records_returned=count),
            )
            return data
    except Exception as exc:
        append_ledger(
            ledger_path,
            ledger_entry(
                endpoint=url,
                params=params,
                status="error",
                http_status=http_status,
                records_returned=0,
                error=f"{type(exc).__name__}: {exc}",
            ),
        )
        return []


async def fetch_paginated(
    session: aiohttp.ClientSession,
    url: str,
    *,
    closed: bool,
    active: bool,
    limit: int,
    ledger_path: Path,
) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    page_limit = min(limit, 100)
    while True:
        params = {
            "closed": str(closed).lower(),
            "active": str(active).lower(),
            "limit": page_limit,
            "offset": offset,
        }
        page = await fetch_json(session, url, params, ledger_path)
        if not isinstance(page, list) or not page:
            break
        rows.extend(page)
        if len(page) < page_limit:
            break
        offset += page_limit
    return rows


def markets_from_event(event: dict[str, Any]) -> list[dict[str, Any]]:
    markets = []
    for key in ("markets", "market", "children"):
        value = event.get(key)
        if isinstance(value, list):
            markets.extend(x for x in value if isinstance(x, dict))
        elif isinstance(value, dict):
            markets.append(value)
    for market in markets:
        if not market.get("eventId") and not market.get("event_id"):
            market["eventId"] = event.get("id") or event.get("eventId")
    return markets


async def fetch_search(session: aiohttp.ClientSession, query: str, ledger_path: Path) -> list[dict]:
    data = await fetch_json(
        session,
        GAMMA_SEARCH_URL,
        {"q": query, "limit": 100, "closed": "true", "active": "false"},
        ledger_path,
    )
    found: list[dict] = []
    if isinstance(data, dict):
        for key in ("markets", "results", "data"):
            value = data.get(key)
            if isinstance(value, list):
                found.extend(x for x in value if isinstance(x, dict))
        events = data.get("events")
        if isinstance(events, list):
            for event in events:
                if isinstance(event, dict):
                    found.extend(markets_from_event(event))
    elif isinstance(data, list):
        found.extend(x for x in data if isinstance(x, dict))
    return found


def load_locked_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return {row.get("market_id", "") for row in csv.DictReader(f) if row.get("market_id")}


def rejection_scope(market: dict[str, Any]) -> str:
    question = str(market.get("question") or market.get("title") or "")
    parsed = parse_market_title(question, str(market.get("description") or ""), str(market.get("slug") or ""))
    if is_club_exhibition_market(parsed):
        return "club_exhibition"
    if not is_dota_market(market):
        return "not_dota"
    if not _is_map_winner_market(market):
        return parsed.get("market_scope") or "not_game_winner"
    return "accepted"


async def collect(args: argparse.Namespace) -> tuple[list[dict], list[dict[str, str]], dict[str, Any]]:
    locked_ids = load_locked_ids(Path(args.locked_manifest))
    ledger_path = Path(args.fetch_ledger)
    raw: list[dict] = []
    normalized: dict[str, dict[str, str]] = {}
    discovered_by_query: Counter[str] = Counter()
    accepted_by_query: Counter[str] = Counter()
    rejected_by_scope_by_query: dict[str, Counter[str]] = defaultdict(Counter)
    async with aiohttp.ClientSession() as session:
        for closed, active in ((True, False), (False, True), (True, True)):
            source = f"gamma_markets_closed_{closed}_active_{active}"
            rows = await fetch_paginated(session, GAMMA_MARKETS_URL, closed=closed, active=active, limit=args.limit, ledger_path=ledger_path)
            for market in rows:
                raw.append({"source": source, "source_universe": "polymarket_gamma", "fetched_at": utc_now(), "payload": market})
                discovered_by_query[source] += 1
                row = normalize_market(market, source, "polymarket_gamma", locked_ids)
                if row:
                    accepted_by_query[source] += 1
                    normalized.setdefault(row["market_id"], row)
                else:
                    rejected_by_scope_by_query[source][rejection_scope(market)] += 1

            event_source = f"gamma_events_closed_{closed}_active_{active}"
            events = await fetch_paginated(session, GAMMA_EVENTS_URL, closed=closed, active=active, limit=args.limit, ledger_path=ledger_path)
            for event in events:
                raw.append({"source": event_source, "source_universe": "polymarket_event", "fetched_at": utc_now(), "payload": event})
                for market in markets_from_event(event):
                    discovered_by_query[event_source] += 1
                    row = normalize_market(market, event_source, "polymarket_event", locked_ids)
                    if row:
                        accepted_by_query[event_source] += 1
                        normalized.setdefault(row["market_id"], row)
                    else:
                        rejected_by_scope_by_query[event_source][rejection_scope(market)] += 1

        for query in args.search_query:
            for market in await fetch_search(session, query, ledger_path):
                source = f"gamma_public_search:{query}"
                raw.append({"source": source, "source_universe": "polymarket_public_search", "fetched_at": utc_now(), "payload": market})
                discovered_by_query[query] += 1
                row = normalize_market(market, source, "polymarket_public_search", locked_ids, discovery_query=query)
                if row:
                    accepted_by_query[query] += 1
                    normalized.setdefault(row["market_id"], row)
                else:
                    rejected_by_scope_by_query[query][rejection_scope(market)] += 1
    summary = {
        "discovered_by_query": dict(discovered_by_query),
        "accepted_by_query": dict(accepted_by_query),
        "rejected_by_scope_by_query": {k: dict(v) for k, v in rejected_by_scope_by_query.items()},
        "normalized_dota_map_markets": len(normalized),
    }
    return raw, sorted(normalized.values(), key=lambda r: (r["start_ts"], r["market_id"])), summary


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--locked-manifest", default="data/locked_execution_audit/locked_market_ids.csv")
    parser.add_argument("--raw-output", default="data/raw/polymarket/markets_raw.jsonl")
    parser.add_argument("--processed-output", default="data/processed/polymarket/dota_market_universe.csv")
    parser.add_argument("--summary-output", default="reports/polymarket_discovery_summary.json")
    parser.add_argument("--fetch-ledger", default="logs/polymarket_discovery_fetches.jsonl")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--search-query", action="append", default=DEFAULT_SEARCH_QUERIES)
    args = parser.parse_args()
    raw, normalized, summary = asyncio.run(collect(args))
    write_jsonl(Path(args.raw_output), raw)
    write_csv(Path(args.processed_output), normalized)
    Path(args.summary_output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.summary_output).write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"raw_payloads={len(raw)} normalized_dota_map_markets={len(normalized)}")
    print(f"wrote {args.raw_output}")
    print(f"wrote {args.processed_output}")
    print(f"wrote {args.summary_output}")
    print(f"wrote {args.fetch_ledger}")


if __name__ == "__main__":
    main()
