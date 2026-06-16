"""Mine shadow_trades for per-(event × price × spread × gt) cells with real edge.

Goal: build a data-driven whitelist of conditions where the event signal
actually has +EV in LIVE conditions (not backtest, not theory — the real
paper trades the bot made).
"""
from __future__ import annotations
import csv
from collections import defaultdict
from itertools import product
from pathlib import Path
from statistics import mean

ROOT = Path('/home/tstuv/dota-poly-signal-pnl-asd')

def fnum(s):
    try: return float(s) if s not in ("", None) else None
    except: return None

rows = []
with (ROOT/"logs/shadow_trades.csv").open() as f:
    for r in csv.DictReader(f):
        if r.get("decision") != "paper_buy_yes": continue
        ep = fnum(r.get("entry_price")); sp = fnum(r.get("spread_at_entry"))
        gt = fnum(r.get("game_time_sec")); m60 = fnum(r.get("markout_60s"))
        if None in (ep, m60): continue
        rows.append({"event": r["event_type"], "ep": ep, "sp": sp or 0,
                     "gt": gt or 0, "m60": m60,
                     "edge": fnum(r.get("executable_edge")) or 0})

def price_b(p):
    if p < 0.30: return "dog_deep"
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

def gt_b(g):
    if g < 600: return "early"
    if g < 1500: return "mid"
    if g < 2400: return "late"
    return "vlate"

# ============================================================
# 1. Per-(event, price) cells
# ============================================================
print(f"=== EVENT × PRICE cells (n>=2) ===")
print(f"  {'event':30s} {'price':>10s} {'n':>3s} {'win%':>5s} {'avg_m60':>9s} {'best':>7s} {'worst':>7s}")
cells = defaultdict(list)
for r in rows: cells[(r["event"], price_b(r["ep"]))].append(r["m60"])
candidates = []
for (ev, pb), ms in cells.items():
    if len(ms) < 2: continue
    w = sum(1 for m in ms if m > 0)
    score = mean(ms) * (len(ms)**0.5)
    candidates.append({"event": ev, "price": pb, "n": len(ms), "win": w/len(ms),
                       "avg": mean(ms), "score": score, "best": max(ms), "worst": min(ms)})
for c in sorted(candidates, key=lambda x: -x["score"])[:20]:
    print(f"  {c['event']:30s} {c['price']:>10s} {c['n']:>3} {c['win']*100:>4.0f}% {c['avg']:+9.4f} {c['best']:+7.3f} {c['worst']:+7.3f}")

# ============================================================
# 2. Per-(event, spread) cells
# ============================================================
print(f"\n=== EVENT × SPREAD cells (n>=2) ===")
print(f"  {'event':30s} {'spread':>8s} {'n':>3s} {'win%':>5s} {'avg_m60':>9s}")
cells2 = defaultdict(list)
for r in rows: cells2[(r["event"], spread_b(r["sp"]))].append(r["m60"])
for (ev, sb), ms in sorted(cells2.items(), key=lambda x: -mean(x[1]) * (len(x[1])**0.5)):
    if len(ms) < 2: continue
    w = sum(1 for m in ms if m > 0)
    print(f"  {ev:30s} {sb:>8s} {len(ms):>3} {w/len(ms)*100:>4.0f}% {mean(ms):+9.4f}")

# ============================================================
# 3. TRIPLE: event × price × spread (the gold)
# ============================================================
print(f"\n=== EVENT × PRICE × SPREAD (n>=2, sorted by EV*sqrt(n)) ===")
print(f"  {'event':28s} {'price':>9s} {'spread':>7s} {'n':>3s} {'win%':>5s} {'avg_m60':>9s}")
cells3 = defaultdict(list)
for r in rows: cells3[(r["event"], price_b(r["ep"]), spread_b(r["sp"]))].append(r["m60"])
triples = []
for (ev, pb, sb), ms in cells3.items():
    if len(ms) < 2: continue
    triples.append({"event": ev, "price": pb, "spread": sb, "n": len(ms),
                    "avg": mean(ms), "win": sum(1 for m in ms if m > 0)/len(ms),
                    "score": mean(ms) * (len(ms)**0.5)})
for t in sorted(triples, key=lambda x: -x["score"])[:15]:
    mark = "✓ KEEP" if t["avg"] > 0.02 and t["n"] >= 3 else (" ◯" if t["avg"] > 0 else " ✗")
    print(f"  {mark} {t['event']:28s} {t['price']:>9s} {t['spread']:>7s} {t['n']:>3} {t['win']*100:>4.0f}% {t['avg']:+9.4f}")

# ============================================================
# 4. Build optimal whitelist from positive cells
# ============================================================
print(f"\n=== OPTIMAL DATA-DRIVEN WHITELIST ===")
print(f"Cells with avg_m60 > 0.01 AND n >= 3 AND win >= 50%:")
keep = [t for t in triples if t["avg"] > 0.01 and t["n"] >= 3 and t["win"] >= 0.50]
keep.sort(key=lambda x: -x["score"])
total_n = sum(t["n"] for t in keep)
total_ev = sum(t["avg"] * t["n"] for t in keep) / total_n if total_n else 0
print(f"  {len(keep)} cells, {total_n} historical trades, avg EV {total_ev:+.4f}c")
print()
for t in keep:
    print(f"  if event=={t['event']} and price={t['price']} and spread={t['spread']}: BUY  # n={t['n']} win={t['win']*100:.0f}% +{t['avg']:.3f}c")

# ============================================================
# 5. Simulation: what if we applied this optimal filter?
# ============================================================
print(f"\n=== OPTIMAL FILTER SIMULATION (on shadow data) ===")
keep_keys = {(t["event"], t["price"], t["spread"]) for t in keep}
filt = [r for r in rows if (r["event"], price_b(r["ep"]), spread_b(r["sp"])) in keep_keys]
if filt:
    ms = [r["m60"] for r in filt]
    w = sum(1 for m in ms if m > 0)
    print(f"  Survived filter:  {len(filt)}/{len(rows)} ({len(filt)/len(rows)*100:.0f}%)")
    print(f"  Win rate:         {w/len(filt)*100:.0f}%")
    print(f"  Avg m60:          {mean(ms):+.4f}")
    print(f"  At $50 stake (≈100 shares):  avg ${mean(ms)*100:+.2f}/trade")
    print(f"  Total P&L:        ${sum(ms)*100:+.0f}  on {len(filt)} trades")
    print()
    print(f"  vs Option C (current):     +7$ on 23 trades = +$0.30/trade")
    print(f"  vs Old model (no filter): -$98 on 63 trades = -$1.55/trade")

# ============================================================
# 6. Honest sample-size warning
# ============================================================
print(f"\n=== HONEST WARNING ===")
print(f"  Best cells have n=3-8 trades. That is NOT statistically significant.")
print(f"  An 80% win rate on n=5 has a 95% CI of 30-99% — useless for sizing.")
print(f"  These rules MIGHT work or might be lucky noise. Run live, audit again.")
