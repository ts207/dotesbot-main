"""SIGNAL IMPROVEMENT — replace the broken edge model with a data-driven lookup.

Diagnosis from audit: executable_edge correlation with realized PnL = -0.04 (noise).
The model can't predict edge — but the DATA shows clear edge in specific subsets.

Approach:
  1. Bin every shadow paper_buy into a (event, game_time, price, spread) bucket.
  2. Compute realized markout_60s per bucket.
  3. Output a LOOKUP-TABLE edge estimate that replaces fair_price calculation.
  4. Use leave-one-out cross-validation to estimate out-of-sample edge.
  5. Stack the bot's existing fair_price WITH the new lookup, pick best combo.

The output is a config-ready policy table the bot can load and use to decide trades.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

ROOT = Path('/home/tstuv/dota-poly-signal-pnl-asd')


def fnum(s):
    try: return float(s) if s not in ("", None) else None
    except: return None


def load():
    out = []
    with (ROOT / "logs" / "shadow_trades.csv").open() as f:
        for row in csv.DictReader(f):
            if row.get("decision") != "paper_buy_yes": continue
            ep = fnum(row.get("entry_price")) or fnum(row.get("ask_at_entry"))
            sp = fnum(row.get("spread_at_entry"))
            gt = fnum(row.get("game_time_sec"))
            m60 = fnum(row.get("markout_60s"))
            m30 = fnum(row.get("markout_30s"))
            ed = fnum(row.get("executable_edge"))
            if None in (ep, sp, gt, m60): continue
            out.append({"event": row["event_type"], "ep": ep, "sp": sp, "gt": gt,
                        "m60": m60, "m30": m30 if m30 is not None else m60,
                        "edge": ed if ed is not None else 0})
    return out


def gt_bucket(gt):
    if gt < 600: return "early"
    if gt < 1500: return "mid"
    if gt < 2400: return "late"
    return "vlate"


def price_bucket(p):
    if p < 0.30: return "deep_dog"
    if p < 0.45: return "dog"
    if p < 0.55: return "toss"
    if p < 0.70: return "lean_fav"
    return "fav"


def spread_bucket(s):
    if s <= 0.02: return "tight"
    if s <= 0.04: return "ok"
    if s <= 0.07: return "wide"
    return "vwide"


trades = load()
print(f"Loaded {len(trades)} shadow paper trades\n")


# ============================================================
# 1) DRILL DOWN: which (event, gt, price, spread) buckets win?
# ============================================================
print("="*80)
print("STEP 1 — Bucket EV table  (event × game_time × price × spread)")
print("="*80)

key = lambda t: (t["event"], gt_bucket(t["gt"]), price_bucket(t["ep"]), spread_bucket(t["sp"]))
buckets = defaultdict(list)
for t in trades:
    buckets[key(t)].append(t["m60"])

# Per-$1 PnL (after 4% slippage). Treat markout as $/share, ep as price/share, so $/$1 = m/ep
def per_d(m, ep): return m / ep - 0.04

bucket_stats = []
for k, ms in buckets.items():
    ev, g, p, s = k
    n = len(ms)
    if n < 1: continue
    avg_m = mean(ms)
    avg_d = mean(per_d(m, 0.5) for m in ms)  # approx, just for ranking
    w = sum(1 for m in ms if m > 0)
    bucket_stats.append({
        "key": k, "n": n, "avg_m60": avg_m,
        "win_rate": w/n, "min": min(ms), "max": max(ms),
    })

# Sort by EV * sample_size (more samples + higher avg = more reliable)
bucket_stats.sort(key=lambda x: -x["avg_m60"] * (x["n"]**0.5))
print(f"\n{'event':25s} {'gt':>6s} {'price':>9s} {'spread':>6s} {'n':>3s} {'avg_m60':>9s} {'win%':>5s}  reliability")
for b in bucket_stats:
    ev, g, p, s = b["key"]
    score = b["avg_m60"] * (b["n"]**0.5)
    marker = "✓" if b["avg_m60"] > 0.02 and b["n"] >= 2 else ("◯" if b["avg_m60"] > 0 else "✗")
    print(f"{marker} {ev:25s} {g:>6s} {p:>9s} {s:>6s} {b['n']:>3} {b['avg_m60']:+8.4f} {b['win_rate']*100:>4.0f}%  {score:+.3f}")


# ============================================================
# 2) MARGINAL FEATURE EFFECTS
# ============================================================
print()
print("="*80)
print("STEP 2 — Marginal effects (one feature at a time)")
print("="*80)

def margins(get_key, label):
    print(f"\n  {label}:")
    groups = defaultdict(list)
    for t in trades:
        groups[get_key(t)].append(t["m60"])
    for k in sorted(groups.keys(), key=lambda x: (str(x))):
        ms = groups[k]
        w = sum(1 for m in ms if m > 0)
        print(f"    {str(k):>18s}  n={len(ms):>2}  avg={mean(ms):+.4f}  win={w/len(ms)*100:>3.0f}%")

margins(lambda t: gt_bucket(t["gt"]), "game_time")
margins(lambda t: price_bucket(t["ep"]), "price level")
margins(lambda t: spread_bucket(t["sp"]), "spread")


# ============================================================
# 3) LEARNED LOOKUP-TABLE PREDICTOR (collapsed key to be less sparse)
# ============================================================
print()
print("="*80)
print("STEP 3 — Build lookup-table predictor (event × gt × spread)")
print("        Drop price bucket from key — too sparse. Use as filter only.")
print("="*80)

key2 = lambda t: (t["event"], gt_bucket(t["gt"]), spread_bucket(t["sp"]))
table = defaultdict(list)
for t in trades:
    if price_bucket(t["ep"]) in ("toss", "lean_fav"): continue   # blacklist toss-up zone
    table[key2(t)].append(t["m60"])

print(f"\n{'event':30s} {'gt':>6s} {'spread':>6s} {'n':>3s} {'avg_m60':>9s} {'win%':>5s}  decision")
keep = {}
for k, ms in sorted(table.items(), key=lambda x: -mean(x[1]) * (len(x[1])**0.5)):
    ev, g, s = k
    n = len(ms)
    avg = mean(ms)
    w = sum(1 for m in ms if m > 0)
    # Keep rule: avg_m60 > 0.02 AND n >= 2 AND spread != vwide
    if avg > 0.02 and n >= 2 and s != "vwide":
        decision = "BUY"; keep[k] = {"n": n, "avg_m60": avg, "win_rate": w/n}
    else:
        decision = "skip"
    print(f"{ev:30s} {g:>6s} {s:>6s} {n:>3} {avg:+8.4f} {w/n*100:>4.0f}%  {decision}")


# ============================================================
# 4) LEAVE-ONE-OUT VALIDATION of the lookup
# ============================================================
print()
print("="*80)
print("STEP 4 — Leave-one-out validation of the learned policy")
print("="*80)
print("For each trade, remove it from the table, refit, and see if its (event,gt,spread)")
print("would have been a BUY using the smaller dataset. Check what realized actually happened.")

def fit_keep(data):
    bk = defaultdict(list)
    for t in data:
        if price_bucket(t["ep"]) in ("toss", "lean_fav"): continue
        bk[(t["event"], gt_bucket(t["gt"]), spread_bucket(t["sp"]))].append(t["m60"])
    return {k: mean(v) for k, v in bk.items() if mean(v) > 0.02 and len(v) >= 2}

results = []
for i, t in enumerate(trades):
    rest = trades[:i] + trades[i+1:]
    policy = fit_keep(rest)
    k = (t["event"], gt_bucket(t["gt"]), spread_bucket(t["sp"]))
    if k in policy and spread_bucket(t["sp"]) != "vwide" \
       and price_bucket(t["ep"]) not in ("toss", "lean_fav"):
        results.append({"would_buy": True, "realized": t["m60"], "key": k})
    else:
        results.append({"would_buy": False, "realized": t["m60"], "key": k})

buys = [r for r in results if r["would_buy"]]
print(f"\n  LOO would-have-bought: {len(buys)}/{len(results)} = {len(buys)/len(results)*100:.0f}% of signals")
if buys:
    realized = [b["realized"] for b in buys]
    w = sum(1 for r in realized if r > 0)
    print(f"  LOO out-of-sample avg markout: {mean(realized):+.4f}")
    print(f"  LOO out-of-sample win rate:    {w/len(buys)*100:.0f}%")
    print(f"  LOO out-of-sample best/worst:  {max(realized):+.4f} / {min(realized):+.4f}")
    print(f"\n  vs raw model trading all 63: avg={mean([t['m60'] for t in trades]):+.4f}, win={sum(1 for t in trades if t['m60']>0)/len(trades)*100:.0f}%")
    improvement = mean(realized) - mean([t["m60"] for t in trades])
    print(f"  Improvement: {improvement:+.4f}/share = {improvement / 0.5 * 100:+.1f}% per $1 staked")


# ============================================================
# 5) EXPORT THE NEW POLICY
# ============================================================
print()
print("="*80)
print("STEP 5 — Export improved policy")
print("="*80)

policy_full = {}
for k, info in keep.items():
    ev, g, s = k
    policy_full[f"{ev}|{g}|{s}"] = {
        "expected_markout": round(info["avg_m60"], 4),
        "win_rate": round(info["win_rate"], 2),
        "n_samples": info["n"],
    }

out_path = ROOT / "logs" / "improved_signal_policy.json"
with out_path.open("w") as f:
    json.dump({"rules": policy_full, "blacklist_price_buckets": ["toss", "lean_fav"],
               "spread_max": 0.04, "version": "learned_v1"}, f, indent=2)
print(f"\n  Written: {out_path}")
print(f"  Total BUY rules: {len(policy_full)}")
print(f"  Strategy:  trade only when (event, gt_bucket, spread_bucket) is in the rules")
print(f"             AND price_bucket NOT in toss/lean_fav AND spread <= 0.04")
print(f"  Sizing:    edge-weight by expected_markout / 0.50 (cap at 0.20 per $1)")


# ============================================================
# 6) SIM the new policy
# ============================================================
print()
print("="*80)
print("STEP 6 — Simulate $500 bankroll with new policy (no-edge, lookup-driven)")
print("="*80)

def simulate(label, trades, frac, slip=0.04, start=500.0):
    import random
    rng = random.Random(42)
    bk = start; peak = start; max_dd = 0; n = 0; wins = 0; pnls = []
    for t in trades:
        k = (t["event"], gt_bucket(t["gt"]), spread_bucket(t["sp"]))
        if spread_bucket(t["sp"]) == "vwide": continue
        if price_bucket(t["ep"]) in ("toss", "lean_fav"): continue
        if k not in keep: continue
        if rng.random() > 0.85: continue
        stake = min(max(5.0, bk * frac), 50.0)
        if stake > bk: continue
        pnl = (t["m60"]/t["ep"] - slip) * stake
        bk += pnl; n += 1; pnls.append(pnl)
        if pnl > 0: wins += 1
        if bk > peak: peak = bk
        dd = peak - bk
        if dd > max_dd: max_dd = dd
        if bk < 50: print("  RUIN"); break
    if n == 0: print(f"  {label}: 0 trades"); return
    print(f"  {label}: n={n}, ${bk:.0f} ({(bk-start)/start*100:+.1f}%), win {wins/n*100:.0f}%, "
          f"avg ${mean(pnls):+.2f}, maxDD {max_dd/peak*100:.0f}%")

for frac in [0.02, 0.05, 0.10, 0.20]:
    simulate(f"COMPOUND {frac*100:.0f}%", trades, frac)
