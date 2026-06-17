"""DOTA-POLY LIVE COMMAND — operational dashboard, redesigned 2026-06-02.

Built around what THIS session proved matters, in priority order:
  1. SYSTEM HEALTH   — the zombie/hang failures: are supervisor+bot+binder
                       actually alive (heartbeats), not just PID-present?
  2. CAPITAL & RISK  — sig-3 balance, today P&L, daily-DD vs kill-switch, exposure
  3. LIVE VALIDATION — paper/live attempts and exits
  4. PIPELINE        — live → bound → in-window → OPEN winner market (the real ceiling)
  5. STRATEGY        — Option 3 config + the 30min tuning
  6. RECENT ACTIVITY — last signals + rejections-by-reason (gate diagnostics)

Run:  python3 dashboard_live.py          (one render)
      python3 dashboard_live.py --loop   (refresh every 15s)
"""
from __future__ import annotations
import csv, json, os, sys, time
from datetime import datetime, timezone, date

G="\033[32m"; R="\033[31m"; Y="\033[33m"; B="\033[34m"; C="\033[36m"; D="\033[2m"; W="\033[1m"; X="\033[0m"
def c(s,col): return f"{col}{s}{X}"
def dot(ok): return c("●",G) if ok else c("●",R)

def fnum(x):
    try: return float(x)
    except: return None

def read_csv(p, tail=None):
    if not os.path.exists(p): return []
    try:
        rows=list(csv.DictReader(open(p)))
        return rows[-tail:] if tail else rows
    except Exception: return []

def hb_age(p):
    try: return time.time()-float(open(p).read().strip())
    except Exception: return None

def proc_alive(pat):
    return os.popen(f"pgrep -f '{pat}' 2>/dev/null").read().strip() != ""

# ---------- PANELS ----------
def panel_health():
    L=[c("▐ SYSTEM HEALTH ▌",W)]
    # (name, pgrep pattern, heartbeat file, fresh-threshold sec) — thresholds match
    # each process's real cadence + margin (supervisor hang-kills past these anyway)
    procs=[("supervisor","supervisor.py",None,0),
           ("bot","python3 main.py","logs/heartbeat",240),
           ("binder","auto_series_binder","logs/binder_heartbeat",200)]
    cells=[]
    for name,pat,hb,thr in procs:
        alive=proc_alive(pat)
        if hb:
            a=hb_age(hb)
            hbtxt = (f"hb {a:.0f}s" if a is not None else "no hb")
            fresh = a is not None and a < thr
            cells.append(f"{dot(alive and fresh)} {name} {c(hbtxt, G if fresh else Y)}")
        else:
            cells.append(f"{dot(alive)} {name}")
    L.append("  "+"   ".join(cells))
    # restart counts from supervisor log
    sup=read_csv("logs/supervisor.log") or []
    rl=[l for l in open("logs/supervisor.log")] if os.path.exists("logs/supervisor.log") else []
    today=date.today().isoformat()
    rtoday=sum(1 for l in rl if "restart #" in l and l.startswith(today.replace("-","-")))
    rs=[l for l in rl if "restart #" in l]
    L.append(c(f"  restarts logged: {len(rs)} total   (auto-recovery active)",D))
    return L

def panel_capital():
    L=[c("▐ CAPITAL & RISK ▌",W)]
    # mode + limits from config
    try:
        from config import (ENABLE_REAL_LIVE_TRADING, MAX_TRADE_USD, MAX_OPEN_USD_PER_MATCH,
                            MAX_DAILY_DRAWDOWN_USD, MAX_TOTAL_LIVE_USD)
        mode = c("LIVE — REAL MONEY",R) if ENABLE_REAL_LIVE_TRADING else c("PAPER",C)
    except Exception as e:
        mode=c("?",Y); MAX_TRADE_USD=MAX_OPEN_USD_PER_MATCH=MAX_DAILY_DRAWDOWN_USD=MAX_TOTAL_LIVE_USD=0
    # balance (sig-3) — best effort, short timeout
    bal=None; appr=None
    try:
        from config import ENABLE_REAL_LIVE_TRADING
        if not ENABLE_REAL_LIVE_TRADING:
            from storage_v2 import StorageV2
            bal = StorageV2().get_simulated_balance(1000.0)
            appr = True
        else:
            from dotenv import dotenv_values
            v=dotenv_values('.env')
            for k,val in v.items():
                if val is not None: os.environ.setdefault(k,val)
            from py_clob_client_v2 import ClobClient, ApiCreds, BalanceAllowanceParams, AssetType
            creds=ApiCreds(api_key=os.getenv('POLY_CLOB_API_KEY'),api_secret=os.getenv('POLY_CLOB_SECRET'),api_passphrase=os.getenv('POLY_CLOB_PASS_PHRASE'))
            cl=ClobClient('https://clob.polymarket.com',137,os.getenv('POLY_PRIVATE_KEY'),creds,signature_type=3,funder=os.getenv('POLY_FUNDER_ADDRESS'))
            r=cl.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
            bal=int(r['balance'])/1e6; appr=all(int(x)>0 for x in r['allowances'].values())
    except Exception:
        pass
    baltxt = (c(f"${bal:.2f}",G) + (" approved" if appr else c(" NOT approved",R))) if bal is not None else c("(balance check unavailable)",D)
    L.append(f"  mode: {mode}    balance: {baltxt}")
    # today realized P&L from paper/live trades
    pnl_today=0.0; n_today=0
    for ledger in ("logs/live_trades.csv","logs/paper_trades.csv"):
        for row in read_csv(ledger):
            ts=row.get("timestamp_utc") or row.get("ts") or ""
            if ts.startswith(date.today().isoformat()):
                p=fnum(row.get("realized_pnl") or row.get("pnl") or 0)
                if p is not None: pnl_today+=p; n_today+=1
    ddpct = (abs(min(0,pnl_today))/MAX_DAILY_DRAWDOWN_USD*100) if MAX_DAILY_DRAWDOWN_USD else 0
    pcol = G if pnl_today>=0 else R
    L.append(f"  today P&L: {c(f'${pnl_today:+.2f}',pcol)} ({n_today} trades)   "
             f"daily-DD: {c(f'${abs(min(0,pnl_today)):.2f}',pcol)}/${MAX_DAILY_DRAWDOWN_USD:.0f} kill ({ddpct:.0f}%)")
    L.append(c(f"  limits: ${MAX_TRADE_USD:.0f}/trade · ${MAX_OPEN_USD_PER_MATCH:.0f}/match · ${MAX_TOTAL_LIVE_USD:.0f} max-deployed",D))
    return L

def panel_validation():
    L=[c("▐ PAPER STRATEGY VALIDATION ▌",W)]
    attempts=read_csv("logs/paper_attempts.csv")
    exits=read_csv("logs/paper_exits.csv")
    recent=[r for r in attempts if (r.get("timestamp_utc") or "").startswith(date.today().isoformat())]
    by_kind={}
    for row in recent:
        kind=row.get("trader_kind") or row.get("event_type") or "unknown"
        by_kind[kind]=by_kind.get(kind,0)+1
    parts=[f"{k}={v}" for k,v in sorted(by_kind.items())] or ["none"]
    L.append(f"  attempts today: {len(recent)}   " + "  ".join(parts))
    L.append(c(f"  exits recorded: {len(exits)}   active strategies: VALUE, EVENT_TRIGGERED_VALUE, DSWING",D))
    return L

def panel_pipeline():
    L=[c("▐ PIPELINE  (live → bound → tradeable) ▌",W)]
    try:
        import asyncio, aiohttp, yaml
        from steam_client import fetch_all_live_games
        KNOWN={19101,19699,19696,19545,16435,19655,19760}
        async def go():
            async with aiohttp.ClientSession() as s: return await fetch_all_live_games(s,include_league=True)
        g=asyncio.run(go())
        live=[x for x in g if x.get('radiant_team') and x.get('dire_team') and (int(x.get('league_id') or 0) in KNOWN or (x.get('spectators') or 0)>=100)]
        md=yaml.safe_load(open('markets.yaml')); bound={str(m.get('dota_match_id')) for m in md.get('markets',[])}
        nb=ninwin=0
        for x in live[:6]:
            gt=(x.get('game_time_sec') or 0)//60
            isb=str(x.get('match_id')) in bound; inw=10<=gt<=30
            nb+=isb; ninwin+=(isb and inw)
            tag=(c("TRADEABLE",G) if (isb and inw) else c("bound",B) if isb else c("unbound",D))
            L.append(f"    {x.get('match_id')} {gt:>2}m {(x.get('radiant_team') or '?')[:11]:<11} vs {(x.get('dire_team') or '?')[:11]:<11} {tag}")
        if not live: L.append(c("    (no tournament-grade games live)",D))
        L.insert(1, c(f"  funnel: {len(live)} live → {nb} bound → {ninwin} in-window tradeable",C))
    except Exception as e:
        L.append(c(f"  pipeline error: {str(e)[:60]}",D))
    return L

def panel_strategy():
    L=[c("▐ STRATEGY ▌",W)]
    try:
        from signal_engine import PRIMARY_TRADE_WHITELIST, _EVENT_MAX_GAME_TIME_SEC
        evs=", ".join(sorted(e.replace("POLL_","") for e in PRIMARY_TRADE_WHITELIST))
        cap=_EVENT_MAX_GAME_TIME_SEC.get("POLL_FIRST_SWING_SETTLE",0)//60
        L.append(c(f"  Option 3 ({len(PRIMARY_TRADE_WHITELIST)}): {evs}",D))
        L.append(c(f"  gt≤{cap}m · ask 0.45-0.85 · hold-to-settle · confidence-sized",D))
    except Exception as e:
        L.append(c(f"  {str(e)[:50]}",D))
    return L

def panel_activity():
    L=[c("▐ RECENT ACTIVITY ▌",W)]
    lat=read_csv("logs/latency.csv", tail=400)
    today=date.today().isoformat()
    todayrows=[r for r in lat if (r.get("timestamp_utc") or "").startswith(today)]
    # rejections by reason today
    from collections import Counter
    rej=Counter()
    for r in todayrows:
        d=r.get("decision"); rr=r.get("skip_reason") or r.get("live_reject_reason")
        if d=="skip" and rr: rej[rr]+=1
    fills=sum(1 for r in todayrows if (r.get("paper_entry_result") or "").startswith("paper_buy") or fnum(r.get("live_filled_size_usd") or 0))
    L.append(f"  today: {len(todayrows)} evals, {c(str(fills)+' fills',G if fills else D)}")
    if rej:
        top=", ".join(f"{k}×{v}" for k,v in rej.most_common(4))
        L.append(c(f"  skips: {top}",D))
    return L

def render():
    os.system("clear")
    now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    bar="═"*72
    print(c(f"╔══ DOTA-POLY LIVE COMMAND {'═'*(44)} {now} ══╗",C))
    for p in (panel_health,panel_capital,panel_validation,panel_pipeline,panel_strategy,panel_activity):
        print()
        try:
            for line in p(): print(line)
        except Exception as e:
            print(c(f"  panel error: {str(e)[:60]}",R))
    print(c("\n"+bar,C))

def main():
    loop="--loop" in sys.argv
    while True:
        render()
        if not loop: break
        time.sleep(15)

if __name__=="__main__":
    main()
