"""Backtest TODAY'S data only (2026-05-26) with combined event + scalp strategy.

Filters events to today's match_ids, runs the wired-in TIER_B whitelist with
premium boost, plus the buy-both-scalp filter, on a shared $500 bankroll.
"""
from __future__ import annotations
import csv, random, sys, yaml
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, stdev

ROOT = Path('/home/tstuv/dota-poly-signal-pnl-asd')
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'scripts'))
from backtest_buy_both_scalp import _load_markets as _load_scalp_markets
from backtest_buy_both_scalp import _load_match_windows, simulate_one as sim_scalp

TODAY = "2026-05-26"
SLIPPAGE_EVENT = 0.04
SLIPPAGE_SCALP = 0.07
FILL_PROB = 0.70
POS_CAP = 50.0
PER_MATCH_CAP = 80.0

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


# Markets
markets = {}
for m in (yaml.safe_load(open(ROOT/"markets.yaml")) or {}).get("markets", []):
    mid = str(m.get("dota_match_id") or "")
    if not mid or mid.startswith("STEAM_MATCH"): continue
    yes = (m.get("yes_team") or "").lower()
    rad = (m.get("steam_radiant_team") or "").lower()
    if not yes: continue
    if mid in markets and markets[mid]["mtype"] == "MAP_WINNER": continue
    markets[mid] = {"yes_tok": str(m.get("yes_token_id") or ""),
                    "yes_is_radiant": yes == rad, "mtype": m.get("market_type", "")}

# Book — TODAY only
book = defaultdict(list)
with (ROOT/"logs/book_events.csv").open() as f:
    for row in csv.DictReader(f):
        ts_raw = row["timestamp_utc"]
        if not ts_raw.startswith(TODAY): continue
        aid = row.get("asset_id", "")
        bid = fnum(row.get("best_bid")); ask = fnum(row.get("best_ask"))
        if None in (bid, ask) or not aid: continue
        ts = parse_ts(ts_raw)
        if ts: book[aid].append((ts, (bid+ask)/2, bid, ask))
for k in book: book[k].sort()

def price_at(aid, ts, side="mid"):
    ticks = book.get(aid, [])
    px = None
    for t, m, b, a in ticks:
        if t > ts: break
        px = {"mid": m, "bid": b, "ask": a}[side]
    return px

# Events — TODAY only
events_actions = []
with (ROOT/"logs/dota_events.csv").open() as f:
    for row in csv.DictReader(f):
        ts_raw = row["timestamp_utc"]
        if not ts_raw.startswith(TODAY): continue
        ev_type = row["event_type"]
        if ev_type not in WHITELIST: continue
        mkt = markets.get(row["match_id"])
        if not mkt: continue
        ts = parse_ts(ts_raw)
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

# Scalp — TODAY only
scalp_markets = _load_scalp_markets()
scalp_windows_all = _load_match_windows()
scalp_actions = []
today_match_ids = set()
with (ROOT/"logs/raw_snapshots.csv").open() as f:
    for row in csv.DictReader(f):
        if row.get("received_at_utc", "").startswith(TODAY):
            today_match_ids.add(row.get("match_id", ""))

for mid, (t0, tN, rl) in scalp_windows_all.items():
    if mid not in today_match_ids: continue
    if mid not in scalp_markets: continue
    r = sim_scalp(mid, scalp_markets[mid], t0, tN, rl)
    if not r: continue
    skew = abs(r["yes_entry"] - r["no_entry"]); s_sum = r["yes_entry"] + r["no_entry"]
    if skew > 0.08 or s_sum > 1.03: continue
    scalp_actions.append({
        "kind": "scalp", "ts": t0, "match_id": mid,
        "per_d_pnl": r["pnl_scratch_and_ride_peak"] - SLIPPAGE_SCALP,
    })

actions = sorted(events_actions + scalp_actions, key=lambda x: x["ts"])
print(f"=== TODAY ({TODAY}) COMBINED STRATEGY BACKTEST ===")
print(f"Match IDs seen today: {len(today_match_ids)}")
print(f"Event actions: {len(events_actions)} (whitelist matches)")
print(f"Scalp actions: {len(scalp_actions)} (filtered)")
print(f"Total: {len(actions)}\n")

# Per-event breakdown
print("Event distribution today:")
by_ev = defaultdict(list)
for a in events_actions:
    by_ev[a["event"]].append(a["per_d_pnl"])
for ev, vs in sorted(by_ev.items(), key=lambda x: -len(x[1])):
    w = sum(1 for v in vs if v > 0)
    print(f"  {ev:35s}  n={len(vs):>3}  avg={mean(vs):+.4f}  win={w/len(vs)*100:>3.0f}%")
print()

# Premium check
premium_today = sum(1 for a in events_actions if is_premium(a))
print(f"Premium-qualified events: {premium_today}/{len(events_actions)}\n")

def simulate(label, *, stake_usd=None, frac=None, premium_mult=2.0, seed=42):
    rng = random.Random(seed)
    bk = 500.0; peak = 500.0; max_dd = 0.0
    n_ev = n_sc = wins_ev = wins_sc = 0
    pnls_ev, pnls_sc = [], []
    per_match = defaultdict(float)
    nofill = 0
    for a in actions:
        if rng.random() > FILL_PROB: nofill += 1; continue
        already = per_match[a["match_id"]]
        if already >= PER_MATCH_CAP: continue
        ideal = bk * frac if frac is not None else stake_usd
        mult = premium_mult if (a["kind"] == "event" and is_premium(a)) else 1.0
        stake = min(max(5.0, ideal * mult), POS_CAP, PER_MATCH_CAP - already)
        if stake < 5.0 or stake > bk: continue
        pnl = a["per_d_pnl"] * stake
        bk += pnl
        per_match[a["match_id"]] += stake
        if a["kind"] == "event":
            n_ev += 1; pnls_ev.append(pnl); wins_ev += int(pnl > 0)
        else:
            n_sc += 1; pnls_sc.append(pnl); wins_sc += int(pnl > 0)
        if bk > peak: peak = bk
        if peak - bk > max_dd: max_dd = peak - bk
        if bk < 50: print(f"  RUIN @ trade {n_ev+n_sc}"); break

    pnls = pnls_ev + pnls_sc
    if not pnls: print(f"{label}: 0 trades"); return
    print(f"\n--- {label} ---")
    print(f"  events: {n_ev}t  win {wins_ev/max(n_ev,1)*100:.0f}%  ${sum(pnls_ev):+.0f}  avg ${mean(pnls_ev) if pnls_ev else 0:+.2f}")
    print(f"  scalps: {n_sc}t  win {wins_sc/max(n_sc,1)*100:.0f}%  ${sum(pnls_sc):+.0f}  avg ${mean(pnls_sc) if pnls_sc else 0:+.2f}")
    print(f"  TOTAL:  {len(pnls)}t  ${bk:.0f} ({(bk-500)/500*100:+.1f}%)  best ${max(pnls):+.2f}  worst ${min(pnls):+.2f}  maxDD {max_dd/peak*100:.0f}%")


print("="*60); print("FIXED $10 — production config"); print("="*60)
simulate("FIXED $10", stake_usd=10)
print("\n" + "="*60); print("OTHER SCENARIOS FOR COMPARISON"); print("="*60)
for s in [5, 25, 50]:
    simulate(f"FIXED ${s}", stake_usd=s)

print("\n" + "="*60); print("MONTE CARLO (500 shuffles)"); print("="*60)
def mc(stake, label):
    finals, ruins = [], 0
    for seed in range(500):
        rng = random.Random(seed)
        shuf = actions[:]; rng.shuffle(shuf)
        bk = 500.0; per_match = defaultdict(float)
        for a in shuf:
            if rng.random() > FILL_PROB: continue
            if per_match[a["match_id"]] >= PER_MATCH_CAP: continue
            mult = 2.0 if (a["kind"] == "event" and is_premium(a)) else 1.0
            stk = min(max(5.0, stake * mult), POS_CAP, PER_MATCH_CAP - per_match[a["match_id"]])
            if stk < 5.0 or stk > bk: continue
            bk += a["per_d_pnl"] * stk
            per_match[a["match_id"]] += stk
            if bk < 50: ruins += 1; break
        finals.append(bk)
    finals.sort()
    print(f"  {label}: 5th=${finals[25]:.0f}  med=${finals[250]:.0f}  95th=${finals[475]:.0f}  ruin={ruins}/500")

for s in [10, 25, 50]: mc(s, f"FIXED ${s}")
