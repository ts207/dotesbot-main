"""One-off watcher for the Amaru vs Chandogs game 8834932746.
Polls Steam game-state + the bot's latency/paper logs every 40s.
Exits (notifying the parent) when:
  - Option 3 FILLS a paper trade on this match  -> EXIT "FILLED"
  - game crosses 35min (past entry window) w/ no fill -> EXIT "WINDOW_CLOSED"
  - game disappears from Steam (ended)            -> EXIT "GAME_ENDED"
  - 45 min hard timeout                           -> EXIT "TIMEOUT"
Prints a running trace so the final output is self-explanatory.
"""
import asyncio, aiohttp, csv, os, time, sys
sys.path.insert(0, ".")
from steam_client import fetch_all_live_games

MID = "8834932746"
START = time.time()
LAT = "logs/latency.csv"
PAP = "logs/paper_attempts.csv"

def _tail_match_rows(path, want_buy=False):
    if not os.path.exists(path):
        return []
    out = []
    try:
        with open(path) as f:
            for line in f:
                if MID in line:
                    out.append(line.rstrip())
    except Exception:
        pass
    return out

async def steam_state():
    try:
        async with aiohttp.ClientSession() as s:
            g = await fetch_all_live_games(s, include_league=True)
        for x in g:
            if str(x.get("match_id")) == MID:
                return x
    except Exception:
        return "err"
    return None  # not in live list

def fmt(x):
    gt = (x.get("game_time_sec") or x.get("game_time") or 0)
    return f"gt={gt//60}m{gt%60:02d}s rad_nw={x.get('radiant_lead')} {x.get('radiant_score')}-{x.get('dire_score')}"

print(f"[watch] start {MID} Amaru vs Chandogs", flush=True)
seen_pap = set()
while True:
    elapsed = int(time.time() - START)
    g = asyncio.run(steam_state())

    # 1) check for a FILLED paper trade on this match
    pap_rows = _tail_match_rows(PAP)
    for r in pap_rows:
        if r in seen_pap:
            continue
        seen_pap.add(r)
        if "paper_buy" in r or "filled" in r.lower():
            print(f"[watch] *** FILLED *** {r[:180]}", flush=True)
            print("EXIT FILLED", flush=True)
            sys.exit(0)

    # 2) latest eval reason for this match
    lat = _tail_match_rows(LAT)
    last_reason = ""
    if lat:
        parts = lat[-1].split(",")
        last_reason = "|".join(p for p in parts if any(k in p for k in (
            "skip","paper_buy","terminal","spread","missing_book","quality",
            "stale","window","primary","band","price_history","edge","FIRST_SWING",
            "PHASE","VALUE","RAPID","DECISIVE")))[:160]

    if g == "err":
        print(f"[watch] +{elapsed}s steam fetch error, retry", flush=True)
    elif g is None:
        print(f"[watch] +{elapsed}s game no longer live (ended). last_eval={last_reason}", flush=True)
        print("EXIT GAME_ENDED", flush=True)
        sys.exit(0)
    else:
        gt = (g.get("game_time_sec") or g.get("game_time") or 0)
        in_win = 600 <= gt <= 2100
        print(f"[watch] +{elapsed}s {fmt(g)} {'[IN WINDOW]' if in_win else ''} evals={len(lat)} last={last_reason}", flush=True)
        if gt > 2100:
            print(f"[watch] game past 35min window, no fill.", flush=True)
            print("EXIT WINDOW_CLOSED", flush=True)
            sys.exit(0)

    if elapsed > 45 * 60:
        print("EXIT TIMEOUT", flush=True)
        sys.exit(0)
    time.sleep(40)
