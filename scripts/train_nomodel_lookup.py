"""Train an empirical lookup table from shadow_trades.csv.

Replaces the hand-coded EXPECTED_MOVE_TABLE in nomodel_event_strategy.py
with a data-driven per-(event × price_bucket × spread_bucket) table.

Output: nomodel_lookup.json — loaded by nomodel_event_strategy at startup.
"""
from __future__ import annotations
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

ROOT = Path('/home/tstuv/dota-poly-signal-pnl-asd')
SHADOW = ROOT / "logs" / "shadow_trades.csv"
OUT = ROOT / "logs" / "nomodel_lookup.json"

def fnum(s):
    try: return float(s) if s not in ("", None) else None
    except: return None

def price_b(p):
    if p < 0.30: return "deep_dog"
    if p < 0.45: return "dog"
    if p < 0.55: return "toss"
    if p < 0.70: return "lean_fav"
    if p < 0.85: return "fav"
    return "fav_high"

def spread_b(s):
    if s <= 0.02: return "tight"
    if s <= 0.04: return "ok"
    if s <= 0.07: return "wide"
    return "vwide"

# Load shadow
rows = []
with SHADOW.open() as f:
    for r in csv.DictReader(f):
        if r.get("decision") != "paper_buy_yes": continue
        ep = fnum(r.get("entry_price")); m60 = fnum(r.get("markout_60s"))
        if ep is None or m60 is None: continue
        rows.append({"ev": r["event_type"], "ep": ep,
                     "sp": fnum(r.get("spread_at_entry")) or 0,
                     "gt": fnum(r.get("game_time_sec")) or 0,
                     "m60": m60,
                     "edge": fnum(r.get("executable_edge")) or 0})

print(f"Loaded {len(rows)} shadow paper trades")

# Build a hierarchy of lookups (finest first, coarser fallbacks)
# 1. event × price × spread
# 2. event × price
# 3. event alone
# 4. global

L3 = defaultdict(list)  # (ev, pb, sb) → m60s
L2 = defaultdict(list)  # (ev, pb)
L1 = defaultdict(list)  # ev
L0 = []                 # global

for r in rows:
    pb = price_b(r["ep"]); sb = spread_b(r["sp"])
    L3[(r["ev"], pb, sb)].append(r["m60"])
    L2[(r["ev"], pb)].append(r["m60"])
    L1[r["ev"]].append(r["m60"])
    L0.append(r["m60"])

def stats(ms):
    if not ms: return None
    return {"n": len(ms), "avg": mean(ms),
            "win_rate": sum(1 for m in ms if m > 0) / len(ms),
            "stdev": stdev(ms) if len(ms) > 1 else 0}

# Output structure
table = {
    "global": stats(L0),
    "by_event": {ev: stats(ms) for ev, ms in L1.items()},
    "by_event_price": {f"{ev}|{pb}": stats(ms) for (ev, pb), ms in L2.items()},
    "by_event_price_spread": {f"{ev}|{pb}|{sb}": stats(ms) for (ev, pb, sb), ms in L3.items()},
}

# Print summary
print(f"\nGlobal: n={table['global']['n']}  avg={table['global']['avg']:+.4f}  win={table['global']['win_rate']*100:.0f}%")
print(f"\nTOP 10 most populous L3 cells (event×price×spread):")
print(f"  {'event':25s} {'price':>9s} {'spread':>7s} {'n':>3s} {'avg':>8s} {'win%':>5s}")
populous = sorted(L3.items(), key=lambda x: -len(x[1]))[:10]
for (ev, pb, sb), ms in populous:
    print(f"  {ev:25s} {pb:>9s} {sb:>7s} {len(ms):>3} {mean(ms):+8.4f} {sum(1 for m in ms if m>0)/len(ms)*100:>4.0f}%")

# Save
OUT.write_text(json.dumps(table, indent=2))
print(f"\nWritten: {OUT}  ({len(L1)} events, {len(L2)} ev×price, {len(L3)} ev×price×spread)")
