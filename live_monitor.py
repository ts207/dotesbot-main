"""Live-bot monitor. Watches the REAL-money bot and exits (notifying the parent)
when something worth surfacing happens:
  - REAL FILL        — a live order got submitted/filled (the big one)
  - GAME TRADEABLE   — a tournament game went live + bound + in 10-30min window
  - HEALTH PROBLEM   — a process heartbeat went stale, or restart-storm
  - KILL-SWITCH      — daily drawdown circuit breaker tripped
  - TIMEOUT (40min)  — periodic check-in even if idle
Prints a running trace; final lines explain the exit.
"""
from __future__ import annotations
import asyncio, aiohttp, csv, os, time, sys, yaml
sys.path.insert(0, ".")
from steam_client import fetch_all_live_games

START = time.time()
KNOWN = {19101,19699,19696,19545,16435,19655,19760}
LIVE_ATT = "logs/live_attempts.csv"
LAT = "logs/latency.csv"

def hb_age(p):
    try: return time.time()-float(open(p).read().strip())
    except Exception: return 1e9

def restart_count():
    try: return sum(1 for l in open("logs/supervisor.log") if "restart #" in l)
    except Exception: return 0

def filled_matches():
    """set of match_ids that have a genuinely FILLED order (filled_size>0).
    Deduped by match so re-logged rows of the same fill don't double-count."""
    out=set()
    if os.path.exists(LIVE_ATT):
        try:
            for r in csv.DictReader(open(LIVE_ATT)):
                fs=r.get("filled_size_usd") or "0"
                if fs and float(fs or 0)>0 and (r.get("order_status")=="filled"):
                    out.add(str(r.get("match_id")))
        except Exception: pass
    return out

async def tradeable_games():
    try:
        async with aiohttp.ClientSession() as s:
            g=await fetch_all_live_games(s, include_league=True)
    except Exception:
        return []
    md=yaml.safe_load(open("markets.yaml")); bound={str(m.get("dota_match_id")) for m in md.get("markets",[])}
    out=[]
    for x in g:
        if not (x.get("radiant_team") and x.get("dire_team")): continue
        if not (int(x.get("league_id") or 0) in KNOWN or (x.get("spectators") or 0)>=100): continue
        gt=(x.get("game_time_sec") or 0)//60
        if str(x.get("match_id")) in bound and 10<=gt<=30:
            out.append((x.get("match_id"),gt,x.get("radiant_team"),x.get("dire_team")))
    return out

def kill_switch_tripped():
    if not os.path.exists(LAT): return False
    try:
        for r in list(csv.DictReader(open(LAT)))[-50:]:
            if "daily_drawdown_circuit_breaker" in (r.get("live_reject_reason") or r.get("skip_reason") or ""):
                return True
    except Exception: pass
    return False

SEEN_FILE = "logs/monitor_seen_games"
def load_seen():
    try: return set(open(SEEN_FILE).read().split())
    except Exception: return set()
def add_seen(mid):
    try:
        with open(SEEN_FILE,"a") as f: f.write(str(mid)+"\n")
    except Exception: pass

SEEN_FILLS_FILE = "logs/monitor_seen_fills"
def load_seen_fills():
    try: return set(open(SEEN_FILLS_FILE).read().split())
    except Exception: return set()
def add_seen_fill(mid):
    try:
        with open(SEEN_FILLS_FILE,"a") as f: f.write(str(mid)+"\n")
    except Exception: pass

print(f"[monitor] LIVE bot watch start", flush=True)
seen_fills = load_seen_fills() | filled_matches()   # don't re-alert already-known fills
base_restarts = restart_count()
seen_games = load_seen()   # match_ids already flagged tradeable in a prior cycle
while True:
    el=int(time.time()-START)
    # health
    bot_hb=hb_age("logs/heartbeat")
    if bot_hb>240:
        print(f"[monitor] +{el}s bot heartbeat STALE {bot_hb:.0f}s — supervisor should be recovering", flush=True)
        print("EXIT HEALTH bot heartbeat stale", flush=True); sys.exit(0)
    rc=restart_count()
    if rc-base_restarts>=3:
        print(f"[monitor] +{el}s RESTART STORM ({rc-base_restarts} restarts since watch start)", flush=True)
        print("EXIT HEALTH restart storm", flush=True); sys.exit(0)
    # kill switch
    if kill_switch_tripped():
        print(f"[monitor] +{el}s DAILY KILL-SWITCH tripped", flush=True)
        print("EXIT KILLSWITCH", flush=True); sys.exit(0)
    # fills — only NEW filled matches (deduped, persisted)
    fm=filled_matches()
    new_fills=fm-seen_fills
    if new_fills:
        for mid in new_fills: add_seen_fill(mid); seen_fills.add(mid)
        print(f"[monitor] +{el}s *** REAL FILL *** new filled match(es): {', '.join(new_fills)}", flush=True)
        print("EXIT FILL", flush=True); sys.exit(0)
    # tradeable games — only flag NEW ones (dedup across re-arms via seen file)
    tg=asyncio.run(tradeable_games())
    new_tg=[t for t in tg if str(t[0]) not in seen_games]
    if new_tg:
        for t in new_tg: add_seen(t[0]); seen_games.add(str(t[0]))
        print(f"[monitor] +{el}s GAME TRADEABLE: "+"; ".join(f"{m} {gt}m {r} v {d}" for m,gt,r,d in new_tg), flush=True)
        print("EXIT TRADEABLE", flush=True); sys.exit(0)
    # already-flagged games still in window: keep watching (for the FILL), log quietly
    if tg and el % 300 < 45:
        print(f"[monitor] +{el}s tracking {len(tg)} in-window game(s) (already flagged), watching for fill", flush=True)
    if el % 300 < 45:
        print(f"[monitor] +{el}s idle — bot hb {bot_hb:.0f}s, fills {len(fm)}, no tradeable games", flush=True)
    if el > 180*60:  # idle check-in cadence; real events (fill/game/health) exit within 45s regardless
        print("EXIT TIMEOUT", flush=True); sys.exit(0)
    time.sleep(45)
