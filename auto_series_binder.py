"""Auto-discover all live Dota tournament matches and bind them to Polymarket
series-winner markets.

Run once or on a loop. Updates markets.yaml in place.

Strategy:
  1. Fetch all open Dota events from Polymarket gamma (paginated)
  2. For each event, find its series-winner market (BO1/BO3/BO5, no "Game N")
  3. Match against currently-live Steam matches by team-name pair
  4. Bind unbound mappings to current Steam match_id

Run: python3 auto_series_binder.py [--loop]
"""
from __future__ import annotations

import asyncio
import aiohttp
import json
import re
import requests
import sys
import time
import yaml
from datetime import datetime, timezone

sys.path.insert(0, ".")
from steam_client import fetch_all_live_games
from team_utils import norm_team


GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


def fetch_polymarket_dota_events() -> list[dict]:
    """Paginate through all open Dota events on Polymarket."""
    seen_slugs: set[str] = set()
    unique: list[dict] = []

    queries = [
        f"{GAMMA}/events?tag_slug=dota-2&closed=false&limit=200",
        f"{GAMMA}/events?tag_slug=dota-2&closed=false&limit=200&offset=200",
        f"{GAMMA}/events?tag_slug=esports&closed=false&limit=200",
    ]
    for url in queries:
        try:
            r = requests.get(url, timeout=20)
            if r.status_code != 200:
                continue
            for e in r.json() or []:
                slug = e.get("slug")
                if not slug or slug in seen_slugs:
                    continue
                seen_slugs.add(slug)
                if "dota" in (e.get("title", "") or "").lower():
                    unique.append(e)
        except Exception as exc:
            print(f"  gamma fetch error: {exc}", flush=True)
    return unique


def extract_series_market(event: dict) -> dict | None:
    """Return the series-winner (BO1/3/5) market from an event, or None."""
    for m in event.get("markets", []):
        q = m.get("question") or ""
        if not q:
            continue
        if "Game" in q:
            continue
        if not any(bo in q for bo in ("BO1", "BO3", "BO5")):
            continue
        if not m.get("acceptingOrders"):
            continue
        return m
    return None


def _book_is_live(token_id: str) -> bool:
    """True if a token's book has TWO-SIDED liquidity in the tradeable range —
    i.e. a game currently being played. Settled games are one-sided (→0/1);
    not-yet-started games are dormant (0.01/0.99 seed only)."""
    if not token_id:
        return False
    try:
        r = requests.get(f"{CLOB}/book?token_id={token_id}", timeout=8)
        bk = r.json() if r.status_code == 200 else {}
    except Exception:
        return False
    bids = [float(b["price"]) for b in bk.get("bids", []) if 0.05 < float(b.get("price", 0)) < 0.95]
    asks = [float(a["price"]) for a in bk.get("asks", []) if 0.05 < float(a.get("price", 0)) < 0.95]
    return bool(bids) and bool(asks)


def extract_live_game_market(event: dict) -> dict | None:
    """2026-06-01 — Return the LIVE single-game (Game-N-Winner / MAP) market.

    Binding the SERIES market (extract_series_market) is wrong for S1: S1
    predicts the GAME winner, and the liquid market for a game-in-progress is
    its Game-N-Winner market. Earlier games are settled (one-sided book); later
    games are dormant. The live game is the Game-N market with two-sided liquidity.
    Prefer this; the caller falls back to the series market if none is live.
    """
    candidates = []
    for m in event.get("markets", []):
        q = m.get("question") or ""
        if "Game" not in q or "Winner" not in q:
            continue
        if m.get("winner") or not m.get("acceptingOrders"):
            continue
        try:
            toks = json.loads(m.get("clobTokenIds", "[]") or "[]")
        except Exception:
            continue
        if len(toks) < 2:
            continue
        candidates.append((q, m, toks))
    # Pick the lowest game-number market that is actually live (two-sided book).
    for q, m, toks in sorted(candidates, key=lambda c: c[0]):
        if _book_is_live(toks[0]):
            return m
    return None


def derive_series_state(event: dict, yes_team: str, no_team: str):
    """(current_game_number, series_score_yes, series_score_no) derived from the
    event's RESOLVED Game-N Winner markets (Polymarket's own outcomes). This is the
    fix for the stale 'G1 0-0' bug — the score now reflects games actually completed.
    Falls back to (1, 0, 0) when nothing is resolved yet."""
    sy = sn = resolved = 0
    yn, nn = norm_team(yes_team), norm_team(no_team)
    for m in event.get("markets", []):
        q = m.get("question") or ""
        if "Game" not in q or "Winner" not in q:
            continue
        try:
            outs = m.get("outcomes")
            if isinstance(outs, str):
                outs = json.loads(outs)
            prices = m.get("outcomePrices")
            if isinstance(prices, str):
                prices = json.loads(prices)
        except Exception:
            continue
        if not outs or not prices or len(outs) != len(prices):
            continue
        win = None
        for i, p in enumerate(prices):
            try:
                if float(p) > 0.95:
                    win = norm_team(outs[i]); break
            except (TypeError, ValueError):
                pass
        if win is None:
            continue  # this game not resolved yet
        resolved += 1
        if win == yn:
            sy += 1
        elif win == nn:
            sn += 1
    return resolved + 1, sy, sn


async def fetch_steam_games() -> list[dict]:
    async with aiohttp.ClientSession() as s:
        return await fetch_all_live_games(s, include_league=True)


def find_live_steam_match(yes_team: str, no_team: str, steam_games: list[dict]) -> dict | None:
    """Find the live Steam match whose team-pair matches the market outcomes."""
    yes_n = norm_team(yes_team)
    no_n = norm_team(no_team)
    # Require meaningful normalized names (≥3 chars) to avoid Chinese/garbage matches
    if not yes_n or not no_n or len(yes_n) < 3 or len(no_n) < 3:
        return None
    # First pass: exact normalized match, game must have started (gt > 0)
    for g in steam_games:
        rn = norm_team(g.get("radiant_team", ""))
        dn = norm_team(g.get("dire_team", ""))
        if not rn or not dn or len(rn) < 3 or len(dn) < 3:
            continue
        if {rn, dn} == {yes_n, no_n} and (g.get("game_time") or g.get("game_time_sec") or 0) > 0:
            return g
    # EMERGENCY OVERRIDE FOR GAME 3 (4ikibamboni vs Nande+4)
    # The user demanded we trade this specific game. Players are anonymous/smurfing.
    if "4iki" in yes_n and "nande" in no_n:
        for g in steam_games:
            if str(g.get("match_id")) == "8843560379":
                g["radiant_team"] = "4ikibamboni"
                g["dire_team"] = "Nande+4"
                return g

    # Fallback: substring containment — require ≥4 char match to avoid false positives
    for g in steam_games:
        rn = norm_team(g.get("radiant_team", ""))
        dn = norm_team(g.get("dire_team", ""))
        if rn and dn and len(rn) >= 3 and len(dn) >= 3:
            yn4 = yes_n[:4]; nn4 = no_n[:4]
            if len(yn4) >= 4 and len(nn4) >= 4:
                if (yn4 in rn and nn4 in dn) or (yn4 in dn and nn4 in rn):
                    return g

    # Pass 3: Player Name Heuristic (for mix-stacks without official team names)
    # Check if a player name contains the market outcome name.
    for g in steam_games:
        players = g.get("players", [])
        rad_names = [norm_team(p.get("name", "") or p.get("hero_name", "")) for p in players if p.get("team") == 0]
        dire_names = [norm_team(p.get("name", "") or p.get("hero_name", "")) for p in players if p.get("team") == 1]
        
        # Helper to check if a target team name is a substring of any player name on a side
        def matches_side(team_n: str, side_names: list[str]) -> bool:
            if not team_n or len(team_n) < 3:
                return False
            t4 = team_n[:4] # match first 4 chars to avoid tiny substrings
            return any(t4 in n for n in side_names if n)

        # Check if radiant matches YES and dire matches NO
        if matches_side(yes_n, rad_names) and matches_side(no_n, dire_names):
            # Mutate to assign team names so we don't return blank teams
            g["radiant_team"] = g.get("radiant_team") or yes_team
            g["dire_team"] = g.get("dire_team") or no_team
            return g
            
        # Check if radiant matches NO and dire matches YES
        if matches_side(no_n, rad_names) and matches_side(yes_n, dire_names):
            g["radiant_team"] = g.get("radiant_team") or no_team
            g["dire_team"] = g.get("dire_team") or yes_team
            return g

    return None


def bind_event_to_steam(market: dict, event: dict, steam_match: dict,
                       markets_yaml: dict, force_map_winner: bool = False) -> bool:
    """Add a new mapping to markets_yaml. Returns True if added.

    force_map_winner=True when binding a live Game-N-Winner market (the market
    settles on THIS game, so it's a MAP_WINNER that S1 is validated on).
    """
    try:
        tokens = json.loads(market["clobTokenIds"])
        outcomes = market.get("outcomes")
        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes)
    except Exception:
        return False
    if len(tokens) < 2 or not outcomes or len(outcomes) < 2:
        return False

    yes_n = norm_team(outcomes[0])
    rad_n = norm_team(steam_match.get("radiant_team", ""))
    side_map = "normal" if yes_n == rad_n else "reversed"
    steam_mid = str(steam_match["match_id"])
    gn, sscore_y, sscore_n = derive_series_state(event, outcomes[0], outcomes[1])

    # Anti-duplicate: a single live Steam match_id must NOT bind to more than one
    # game market of the same series. The same-teams markets (Game 1/2/3) all match
    # the one live game via find_live_steam_match, so without this the live match_id
    # lands on multiple MAP_WINNER markets -> validator kills them all (how Grind G2
    # got skipped). If this match_id is already bound to ANY MAP_WINNER for these
    # teams, don't bind it again.
    _yt, _nt = norm_team(outcomes[0]), norm_team(outcomes[1])
    for x in markets_yaml.get("markets", []):
        xmid = str(x.get("dota_match_id") or "")
        # (1) this MARKET (condition) is already bound to a real game -> never rebind it.
        if x.get("condition_id") == market["conditionId"] and xmid.isdigit():
            return False
        # (2) this live MATCH is already bound to another game of the SAME series ->
        # don't let one live game land on multiple game markets (the Grind-G2 duplicate).
        if (xmid == steam_mid and x.get("market_type") == "MAP_WINNER"
                and {norm_team(x.get("yes_team")), norm_team(x.get("no_team"))} == {_yt, _nt}):
            return False

    markets_yaml["markets"].append({
        "name": market["question"],
        "condition_id": market["conditionId"],
        "market_id": str(market["id"]),
        "market_type": "MAP_WINNER" if force_map_winner else "MATCH_WINNER",
        "series_type": "1",
        "p_next_yes": 0.5,
        "series_score_yes": sscore_y,
        "series_score_no": sscore_n,
        "current_game_number": gn,
        "yes_team": outcomes[0],
        "no_team": outcomes[1],
        "yes_token_id": tokens[0],
        "no_token_id": tokens[1],
        "dota_match_id": steam_mid,
        "confidence": 1.0,
        "scheduled_start_utc": (event.get("startDate") or "")[:19].replace("T", " "),
        "auto_mapped_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "auto_mapped_source": "auto_series_binder",
        "steam_radiant_team": steam_match.get("radiant_team"),
        "steam_dire_team": steam_match.get("dire_team"),
        "steam_side_mapping": side_map,
    })
    return True


def run_once(verbose: bool = True) -> int:
    """Single pass. Returns number of new bindings added."""
    events = fetch_polymarket_dota_events()
    if verbose:
        print(f"  Polymarket Dota events: {len(events)}", flush=True)

    steam_games = asyncio.run(fetch_steam_games())
    valid_live_games = [g for g in steam_games if (g.get("game_time") or g.get("game_time_sec") or 0) > 0]

    if verbose:
        print(f"  Live Steam matches: {len(valid_live_games)}", flush=True)

    with open("markets.yaml") as fp:
        md = yaml.safe_load(fp)

    added = 0
    for event in events:
        # 2026-06-01 — Prefer the LIVE single-game (Game-N-Winner) market over the
        # series market. S1 predicts the GAME winner and the live game's Game-N
        # market is the right (and liquid) one. Fall back to the series market
        # only if no game market is currently live (two-sided book).
        live_game = extract_live_game_market(event)
        market = live_game or extract_series_market(event)
        is_map = live_game is not None
        if not market:
            continue
        try:
            outcomes = market.get("outcomes")
            if isinstance(outcomes, str):
                outcomes = json.loads(outcomes)
            if not outcomes or len(outcomes) < 2:
                continue
        except Exception:
            continue
        match = find_live_steam_match(outcomes[0], outcomes[1], valid_live_games)
        if not match:
            continue
        if bind_event_to_steam(market, event, match, md, force_map_winner=is_map):
            added += 1
            if verbose:
                _kind = "GAME" if is_map else "series"
                print(f"    + [{_kind}] {market.get('question','')[:55]} → Steam {match['match_id']}", flush=True)

        # 2026-06-04 — GAME 3 IS THE MONEYLINE. On a BO3 decider (series 1-1, game 3),
        # winning the live game wins the series, so the MATCH_WINNER moneyline == the live
        # game winner (is_game3_match_proxy). The moneyline is the LIQUID market for game 3
        # (the Game-N MAP market is often dead). Without binding it to the live Steam match
        # it keeps a placeholder match_id and the decider is never traded. Bind it too.
        series_mkt = extract_series_market(event)
        if series_mkt is not None and series_mkt is not market:
            try:
                so = series_mkt.get("outcomes")
                if isinstance(so, str):
                    so = json.loads(so)
            except Exception:
                so = None
            if so and len(so) >= 2:
                _gn, _sy, _sn = derive_series_state(event, so[0], so[1])
                if _gn == 3 and _sy == 1 and _sn == 1:  # BO3 decider: game winner == series winner
                    if bind_event_to_steam(series_mkt, event, match, md, force_map_winner=False):
                        added += 1
                        if verbose:
                            print(f"    + [G3-moneyline] {series_mkt.get('question','')[:48]} → Steam {match['match_id']}", flush=True)

    # Refresh series state on EXISTING mappings — games complete between binds, so
    # the once-set score goes stale (the 'G1 0-0' bug). Re-derive each pass from the
    # event's resolved game markets and update in place.
    by_cond = {}
    for ev in events:
        for mm in ev.get("markets", []):
            if mm.get("conditionId"):
                by_cond[mm["conditionId"]] = ev
    updated = 0
    for x in md.get("markets", []):
        ev = by_cond.get(x.get("condition_id"))
        if not ev or not x.get("yes_team") or not x.get("no_team"):
            continue
        gn, sy, sn = derive_series_state(ev, x["yes_team"], x["no_team"])
        cur = (x.get("current_game_number"), x.get("series_score_yes"), x.get("series_score_no"))
        if cur != (gn, sy, sn):
            x["current_game_number"], x["series_score_yes"], x["series_score_no"] = gn, sy, sn
            updated += 1
        # MATCH_WINNER (series moneyline) spans the whole series — the LIVE game changes
        # each map. Re-point it to the CURRENT live Steam match (and fix side mapping) so
        # the game-3 decider proxy trades the LIVE game, not a finished earlier one. This
        # is the fix for a moneyline frozen on a prior game's match_id (Grind/Carstensz G3).
        if x.get("market_type") == "MATCH_WINNER":
            lm = find_live_steam_match(x["yes_team"], x["no_team"], valid_live_games)
            if lm and str(lm.get("match_id")) != str(x.get("dota_match_id")):
                x["dota_match_id"] = str(lm["match_id"])
                x["steam_radiant_team"] = lm.get("radiant_team")
                x["steam_dire_team"] = lm.get("dire_team")
                x["steam_side_mapping"] = ("normal" if norm_team(x["yes_team"]) == norm_team(lm.get("radiant_team", "")) else "reversed")
                x["confidence"] = 1.0
                updated += 1

    if added or updated:
        with open("markets.yaml", "w") as fp:
            yaml.safe_dump(md, fp, sort_keys=False, default_flow_style=False)
    if verbose:
        print(f"  Added {added} new bindings, refreshed {updated} series states.", flush=True)
    return added


def main():
    loop = "--loop" in sys.argv
    interval = 30   # 2026-05-31 — reduced from 60s to catch games at kick-off
    while True:
        try:
            run_once(verbose=True)
        except Exception as exc:
            print(f"  run_once error: {exc}", flush=True)
        # 2026-06-01 — watchdog heartbeat (supervisor monitors this for hangs).
        # The binder zombied for 4h once (loop frozen, PID alive); this lets the
        # supervisor detect and restart it.
        try:
            with open("logs/binder_heartbeat", "w") as _hb:
                _hb.write(str(time.time()))
        except Exception:
            pass
        if not loop:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()
