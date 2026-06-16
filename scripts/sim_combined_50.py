"""$50/trade combined backtest with raised per-match cap to keep volume.

At $50/trade, the existing $80/match cap binds to 1 trade per match → strategy
loses ~75% of signals. Test with $200, $300 caps to find the right tradeoff.
"""
from __future__ import annotations
import csv, random, sys, yaml
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean

ROOT = Path('/home/tstuv/dota-poly-signal-pnl-asd')
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'scripts'))
from backtest_buy_both_scalp import _load_markets as _ls
from backtest_buy_both_scalp import _load_match_windows, simulate_one as sim_scalp

SLIPPAGE_EVENT = 0.04; SLIPPAGE_SCALP = 0.07; FILL_PROB = 0.70
WHITELIST = {
    "POLL_BUYBACK_CAPITULATION", "OBJECTIVE_CONVERSION_T2", "POLL_LATE_FIGHT_FLIP",
    "POLL_VALUE_DISAGREEMENT", "POLL_STRUCTURAL_DOMINANCE", "POLL_KILL_BURST_CONFIRMED",
    "POLL_COMEBACK_RECOVERY", "POLL_FIGHT_SWING", "POLL_DECISIVE_STOMP",
}

def parse_ts(s):
    try: return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except: return 0

def fnum(s):
    try: return float(s) if s not in ("", None) else None
    except: return None

def is_premium(ev):
    if ev["event"] == "POLL_LATE_FIGHT_FLIP" and ev["confidence"] >= 0.9: return True
    if ev["event"] == "POLL_VALUE_DISAGREEMENT" and abs(ev["nw_delta"]) >= 2000: return True
    if ev["event"] == "POLL_KILL_BURST_CONFIRMED" and abs(ev["nw_delta"]) >= 5000: return True
    return False

markets = {}
for m in (yaml.safe_load(open(ROOT/"markets.yaml")) or {}).get("markets", []):
    mid = str(m.get("dota_match_id") or "")
    if not mid or mid.startswith("STEAM_MATCH"): continue
    yes = (m.get("yes_team") or "").lower(); rad = (m.get("steam_radiant_team") or "").lower()
    if not yes: continue
    if mid in markets and markets[mid]["mtype"] == "MAP_WINNER": continue
    markets[mid] = {"yes_tok": str(m.get("yes_token_id") or ""), "yes_is_radiant": yes == rad,
                    "mtype": m.get("market_type", "")}

book = defaultdict(list)
with (ROOT/"logs/book_events.csv").open() as f:
    for row in csv.DictReader(f):
        aid = row.get("asset_id", ""); bid = fnum(row.get("best_bid")); ask = fnum(row.get("best_ask"))
        if None in (bid, ask) or not aid: continue
        ts = parse_ts(row["timestamp_utc"])
        if ts: book[aid].append((ts, (bid+ask)/2, bid, ask))
for k in book: book[k].sort()

def price_at(aid, ts):
    px = None
    for t, m, b, a in book.get(aid, []):
        if t > ts: break
        px = (m, b, a)
    return px

events_actions = []
with (ROOT/"logs/dota_events.csv").open() as f:
    for row in csv.DictReader(f):
        ev_type = row["event_type"]
        if ev_type not in WHITELIST: continue
        mkt = markets.get(row["match_id"])
        if not mkt: continue
        ts = parse_ts(row["timestamp_utc"])
        if not ts: continue
        p0 = price_at(mkt["yes_tok"], ts); p60 = price_at(mkt["yes_tok"], ts+60_000)
        if not p0 or not p60: continue
        ask0 = p0[2]; m60 = p60[0]
        if ask0 is None or m60 is None: continue
        nw = fnum(row.get("networth_delta")) or 0
        direction = (row.get("direction") or "").lower()
        is_rad = "radiant" in direction if direction else nw > 0
        raw_60 = m60 - ask0
        signed = raw_60 if mkt["yes_is_radiant"] == is_rad else -raw_60
        events_actions.append({
            "kind": "event", "ts": ts, "match_id": row["match_id"], "event": ev_type, "ep": ask0,
            "per_d_pnl": signed/ask0 - SLIPPAGE_EVENT,
            "confidence": fnum(row.get("event_confidence")) or 0,
            "nw_delta": nw,
        })

scalp_markets = _ls(); scalp_windows = _load_match_windows()
scalp_actions = []
for mid, (t0, tN, rl) in scalp_windows.items():
    if mid not in scalp_markets: continue
    r = sim_scalp(mid, scalp_markets[mid], t0, tN, rl)
    if not r: continue
    skew = abs(r["yes_entry"] - r["no_entry"]); s_sum = r["yes_entry"] + r["no_entry"]
    if skew > 0.08 or s_sum > 1.03: continue
    scalp_actions.append({"kind": "scalp", "ts": t0, "match_id": mid,
                          "per_d_pnl": r["pnl_scratch_and_ride_peak"] - SLIPPAGE_SCALP})

actions = sorted(events_actions + scalp_actions, key=lambda x: x["ts"])
print(f"=== $50/TRADE COMBINED, RAISED MATCH CAPS ===")
print(f"Events: {len(events_actions)}  Scalps: {len(scalp_actions)}  Total: {len(actions)}\n")

def simulate(label, *, stake, match_cap, pos_cap=50, premium_mult=2.0, seed=42, start=500.0):
    rng = random.Random(seed)
    bk = start; peak = start; max_dd = 0
    n_ev = n_sc = wins_ev = wins_sc = 0
    pnls_ev, pnls_sc = [], []
    per_match = defaultdict(float)
    for a in actions:
        if rng.random() > FILL_PROB: continue
        already = per_match[a["match_id"]]
        if already >= match_cap: continue
        mult = premium_mult if (a["kind"] == "event" and is_premium(a)) else 1.0
        sk = min(max(5.0, stake * mult), pos_cap, match_cap - already)
        if sk < 5.0 or sk > bk: continue
        pnl = a["per_d_pnl"] * sk
        bk += pnl
        per_match[a["match_id"]] += sk
        if a["kind"] == "event": n_ev += 1; pnls_ev.append(pnl); wins_ev += int(pnl > 0)
        else: n_sc += 1; pnls_sc.append(pnl); wins_sc += int(pnl > 0)
        if bk > peak: peak = bk
        if peak - bk > max_dd: max_dd = peak - bk
        if bk < 50: print(f"  RUIN @ {n_ev+n_sc}"); break
    pnls = pnls_ev + pnls_sc
    if not pnls: print(f"{label}: 0 trades"); return None
    return {"label": label, "n_total": len(pnls), "n_ev": n_ev, "n_sc": n_sc,
            "wins_ev": wins_ev, "wins_sc": wins_sc, "final": bk,
            "pnl_ev": sum(pnls_ev), "pnl_sc": sum(pnls_sc),
            "best": max(pnls), "worst": min(pnls), "max_dd_pct": max_dd/peak*100}


def print_row(r):
    if r is None: return
    win = (r["wins_ev"] + r["wins_sc"]) / r["n_total"] * 100
    print(f"  {r['label']:>22s}  n={r['n_total']:>3}  ${r['final']:>5.0f}  ({(r['final']-500)/500*100:+.0f}%)  "
          f"win {win:>3.0f}%  best ${r['best']:+.0f}  worst ${r['worst']:+.0f}  DD {r['max_dd_pct']:.0f}%")


print("="*80)
print("$50/trade — vary per-match cap to find frequency sweet spot")
print("="*80)
print(f"  {'config':>22s}  {'n':>3}  {'$final':>5}      {'win%':>4}  {'best':>8}  {'worst':>8}  {'DD':>4}")
for cap in [80, 150, 200, 300, 500, 1000]:
    print_row(simulate(f"$50 cap=${cap}", stake=50, match_cap=cap, pos_cap=50))

print()
print("="*80)
print("MONTE CARLO @ $50/trade, varying caps (500 shuffles)")
print("="*80)
def mc(stake, match_cap):
    finals, ruins = [], 0
    for seed in range(500):
        rng = random.Random(seed)
        shuf = actions[:]; rng.shuffle(shuf)
        bk = 500.0; pm = defaultdict(float)
        for a in shuf:
            if rng.random() > FILL_PROB: continue
            if pm[a["match_id"]] >= match_cap: continue
            mult = 2.0 if (a["kind"] == "event" and is_premium(a)) else 1.0
            sk = min(max(5.0, stake * mult), 50.0, match_cap - pm[a["match_id"]])
            if sk < 5.0 or sk > bk: continue
            bk += a["per_d_pnl"] * sk
            pm[a["match_id"]] += sk
            if bk < 50: ruins += 1; break
        finals.append(bk)
    finals.sort()
    print(f"  $50 cap=${match_cap}:  5th=${finals[25]:>4.0f}  med=${finals[250]:>4.0f}  95th=${finals[475]:>4.0f}  ruin={ruins}/500")

for cap in [80, 150, 200, 300, 500]:
    mc(50, cap)

print()
print("="*80)
print("If you raise position cap to $50 — what's the impact?")
print("(Currently POS_CAP=$50, so $50 stake never gets premium 2x boost.)")
print("="*80)
for pcap in [50, 75, 100, 150]:
    print_row(simulate(f"$50 pos=${pcap} mcap=$300", stake=50, match_cap=300, pos_cap=pcap))
