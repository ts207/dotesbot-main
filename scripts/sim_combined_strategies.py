"""COMBINED BACKTEST: event-whitelist + buy-both-scalp on shared $500 bankroll.

Streams BOTH strategies as time-ordered actions and tracks one bankroll:
  - EVENT (1059 dota_events, new TIER_B whitelist + premium 2x)
  - SCALP (48 matches, filtered: skew<=0.08 AND sum<=1.03)

Per-match cap: $80 to prevent both strategies stacking on one game.
"""
from __future__ import annotations
import csv, random, yaml
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, stdev

ROOT = Path('/home/tstuv/dota-poly-signal-pnl-asd')
import sys; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'scripts'))
from backtest_buy_both_scalp import _load_markets as _load_scalp_markets
from backtest_buy_both_scalp import _load_match_windows, simulate_one as sim_scalp

SLIPPAGE_EVENT = 0.04
SLIPPAGE_SCALP_PAIR = 0.07     # round-trip on TWO sides (buy YES+NO, sell both)
FILL_PROB = 0.70
POS_CAP = 50.0
PER_MATCH_CAP = 80.0

# Event whitelist matches event_taxonomy.py TIER_B (current production)
EVENT_WHITELIST = {
    "POLL_BUYBACK_CAPITULATION",
    "OBJECTIVE_CONVERSION_T2", "POLL_LATE_FIGHT_FLIP", "POLL_VALUE_DISAGREEMENT",
    "POLL_STRUCTURAL_DOMINANCE", "POLL_KILL_BURST_CONFIRMED", "POLL_COMEBACK_RECOVERY",
    "POLL_FIGHT_SWING", "POLL_DECISIVE_STOMP",
}
EVENT_BLACKLIST = {
    "POLL_STOMP_THROW_CONFIRMED", "POLL_LEAD_FLIP_WITH_KILLS",
    "POLL_MAJOR_COMEBACK_RECOVERY", "POLL_RAPID_STOMP",
    "POLL_ULTRA_LATE_FIGHT_FLIP", "BLOODY_EVEN_FIGHT",
}

def is_premium_event(ev):
    if ev["event"] == "POLL_LATE_FIGHT_FLIP" and ev["confidence"] >= 0.9: return True
    if ev["event"] == "POLL_VALUE_DISAGREEMENT" and abs(ev["nw_delta"]) >= 2000: return True
    if ev["event"] == "POLL_KILL_BURST_CONFIRMED" and abs(ev["nw_delta"]) >= 5000: return True
    return False

def is_filtered_scalp(r):
    skew = abs(r["yes_entry"] - r["no_entry"])
    s_sum = r["yes_entry"] + r["no_entry"]
    return skew <= 0.08 and s_sum <= 1.03


def parse_ts(s):
    try: return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except: return 0


def fnum(s):
    try: return float(s) if s not in ("", None) else None
    except: return None


# Load markets for events
event_markets = {}
for m in (yaml.safe_load(open(ROOT/"markets.yaml")) or {}).get("markets", []):
    mid = str(m.get("dota_match_id") or "")
    if not mid or mid.startswith("STEAM_MATCH"): continue
    yes = (m.get("yes_team") or "").lower()
    rad = (m.get("steam_radiant_team") or "").lower()
    if not yes: continue
    if mid in event_markets and event_markets[mid]["mtype"] == "MAP_WINNER": continue
    event_markets[mid] = {"yes_tok": str(m.get("yes_token_id") or ""),
                          "yes_is_radiant": yes == rad, "mtype": m.get("market_type", "")}

# Load book
book = defaultdict(list)
with (ROOT/"logs/book_events.csv").open() as f:
    for row in csv.DictReader(f):
        aid = row.get("asset_id", "")
        bid = fnum(row.get("best_bid")); ask = fnum(row.get("best_ask"))
        if None in (bid, ask) or not aid: continue
        ts = parse_ts(row["timestamp_utc"])
        if ts: book[aid].append((ts, (bid+ask)/2, bid, ask))
for k in book: book[k].sort()

def price_at(aid, ts, side="mid"):
    ticks = book.get(aid, [])
    px = None
    for t, m, b, a in ticks:
        if t > ts: break
        px = {"mid": m, "bid": b, "ask": a}[side]
    return px

# Build event-action list
events_actions = []
with (ROOT/"logs/dota_events.csv").open() as f:
    for row in csv.DictReader(f):
        ev_type = row["event_type"]
        if ev_type not in EVENT_WHITELIST or ev_type in EVENT_BLACKLIST: continue
        mkt = event_markets.get(row["match_id"])
        if not mkt: continue
        ts = parse_ts(row["timestamp_utc"])
        if not ts: continue
        ask0 = price_at(mkt["yes_tok"], ts, "ask")
        m60 = price_at(mkt["yes_tok"], ts+60_000, "mid")
        if None in (ask0, m60): continue
        nw = fnum(row.get("networth_delta")) or 0
        direction = (row.get("direction") or "").lower()
        is_rad = "radiant" in direction if direction else nw > 0
        raw_60 = m60 - ask0
        signed = raw_60 if mkt["yes_is_radiant"] == is_rad else -raw_60
        events_actions.append({
            "kind": "event", "ts": ts, "match_id": row["match_id"],
            "event": ev_type, "ep": ask0,
            "per_d_pnl": signed/ask0 - SLIPPAGE_EVENT,
            "confidence": fnum(row.get("event_confidence")) or 0,
            "nw_delta": nw,
        })

# Build scalp-action list (one per match window)
scalp_markets = _load_scalp_markets()
scalp_windows = _load_match_windows()
scalp_actions = []
for mid, (t0, tN, rl) in scalp_windows.items():
    if mid not in scalp_markets: continue
    r = sim_scalp(mid, scalp_markets[mid], t0, tN, rl)
    if not r or not is_filtered_scalp(r): continue
    scalp_actions.append({
        "kind": "scalp", "ts": t0, "match_id": mid,
        "per_d_pnl": r["pnl_scratch_and_ride_peak"] - SLIPPAGE_SCALP_PAIR,
    })

actions = sorted(events_actions + scalp_actions, key=lambda x: x["ts"])
print(f"=== COMBINED STRATEGY BACKTEST ===")
print(f"Event actions: {len(events_actions)}  (whitelist: {sorted(EVENT_WHITELIST)})")
print(f"Scalp actions: {len(scalp_actions)} (filtered)")
print(f"Total actions: {len(actions)} time-ordered")
print(f"Slippage event: {SLIPPAGE_EVENT}, scalp: {SLIPPAGE_SCALP_PAIR}, fill {FILL_PROB*100:.0f}%, "
      f"per-trade cap ${POS_CAP}, per-match cap ${PER_MATCH_CAP}\n")


def simulate(label, *, stake_usd=None, frac=None, premium_mult=2.0, seed=42, start=500.0):
    rng = random.Random(seed)
    bk = start; peak = start; max_dd = 0
    n_ev = n_sc = wins_ev = wins_sc = 0
    pnls_ev = []; pnls_sc = []
    per_match = defaultdict(float)
    nofill = 0
    for a in actions:
        if rng.random() > FILL_PROB: nofill += 1; continue
        # Per-match cap
        already = per_match[a["match_id"]]
        if already >= PER_MATCH_CAP: continue

        ideal = bk * frac if frac is not None else stake_usd
        mult = 1.0
        if a["kind"] == "event" and is_premium_event(a):
            mult = premium_mult
        stake = min(max(5.0, ideal * mult), POS_CAP)
        stake = min(stake, PER_MATCH_CAP - already)
        if stake < 5.0 or stake > bk: continue

        pnl = a["per_d_pnl"] * stake
        bk += pnl
        per_match[a["match_id"]] += stake

        if a["kind"] == "event":
            n_ev += 1; pnls_ev.append(pnl)
            if pnl > 0: wins_ev += 1
        else:
            n_sc += 1; pnls_sc.append(pnl)
            if pnl > 0: wins_sc += 1
        if bk > peak: peak = bk
        if peak - bk > max_dd: max_dd = peak - bk
        if bk < 50: print(f"  *** RUIN at action {n_ev+n_sc} ***"); break

    pnls = pnls_ev + pnls_sc
    if not pnls: print(f"{label}: 0 trades"); return
    print(f"\n--- {label} ---")
    print(f"  events: {n_ev} trades, win {wins_ev/max(n_ev,1)*100:.0f}%, P&L ${sum(pnls_ev):+.0f}, avg ${mean(pnls_ev) if pnls_ev else 0:+.2f}")
    print(f"  scalps: {n_sc} trades, win {wins_sc/max(n_sc,1)*100:.0f}%, P&L ${sum(pnls_sc):+.0f}, avg ${mean(pnls_sc) if pnls_sc else 0:+.2f}")
    print(f"  TOTAL : {len(pnls)} trades, ${bk:.0f} ({(bk-start)/start*100:+.1f}%), win {(wins_ev+wins_sc)/len(pnls)*100:.0f}%")
    print(f"  avg/trade ${mean(pnls):+.2f}  best ${max(pnls):+.2f}  worst ${min(pnls):+.2f}  maxDD {max_dd/peak*100:.0f}%")


print("="*60)
print("FIXED SIZING")
print("="*60)
for s in [10, 25, 50]:
    simulate(f"FIXED ${s}", stake_usd=s)

print("\n" + "="*60)
print("COMPOUNDING SIZING")
print("="*60)
for f in [0.02, 0.05, 0.10, 0.15, 0.20]:
    simulate(f"COMPOUND {f*100:.0f}%", frac=f)

print("\n" + "="*60)
print("COMPOUNDING + PREMIUM 3x BOOST (instead of 2x)")
print("="*60)
for f in [0.05, 0.10, 0.20]:
    simulate(f"COMPOUND {f*100:.0f}% pre3x", frac=f, premium_mult=3.0)

# Monte carlo
print("\n" + "="*60)
print("MONTE CARLO (500 shuffles, FIXED $25)")
print("="*60)
def mc(stake, label):
    finals, ruins = [], 0
    for seed in range(500):
        rng = random.Random(seed)
        shuf = actions[:]; rng.shuffle(shuf)
        bk = 500.0
        per_match = defaultdict(float)
        for a in shuf:
            if rng.random() > FILL_PROB: continue
            already = per_match[a["match_id"]]
            if already >= PER_MATCH_CAP: continue
            mult = 2.0 if (a["kind"] == "event" and is_premium_event(a)) else 1.0
            stk = min(max(5.0, stake * mult), POS_CAP, PER_MATCH_CAP - already)
            if stk < 5.0 or stk > bk: continue
            bk += a["per_d_pnl"] * stk
            per_match[a["match_id"]] += stk
            if bk < 50: ruins += 1; break
        finals.append(bk)
    finals.sort()
    print(f"  {label}: 5th=${finals[25]:.0f}  med=${finals[250]:.0f}  95th=${finals[475]:.0f}  ruin={ruins}/500")

for s in [10, 25, 50]:
    mc(s, f"FIXED ${s}")
