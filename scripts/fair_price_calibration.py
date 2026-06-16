"""D: Fair-price calibration audit.

Measures systematic bias in fair_price at entry vs actual settlement price.
If the model consistently predicts fair=0.70 but the market settles at 0.82,
that's a +0.12 upward bias the model is missing → all edges are understated.

Uses the stomps-promoted backtest CSV (the largest recent run with settlement data).
Falls back to the relaxed backtest CSV.
"""
from __future__ import annotations

import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Try the largest available backtest CSV with pnl_settle
CANDIDATES = [
    ROOT / "validations" / "backtest_2026_05_26_stomps_promoted.csv",
    ROOT / "validations" / "backtest_2026_05_25_relaxed.csv",
    ROOT / "validations" / "backtest_2026_05_25.csv",
]

csv_path = None
for c in CANDIDATES:
    if c.exists():
        csv_path = c
        break

if csv_path is None:
    print("ERROR: no backtest CSV found"); sys.exit(1)

print(f"Using {csv_path.name}")

rows = []
with csv_path.open(encoding="utf-8") as f:
    for row in csv.DictReader(f):
        fp_str = row.get("fair_price", "")
        settle_str = row.get("pnl_settle", "")
        fill_str = row.get("fill_price", "")
        if not fp_str or not fill_str:
            continue
        fp = float(fp_str)
        fill = float(fill_str)
        # pnl_settle = (settle_px - fill) * size_usd
        # So settle_px = fill + pnl_settle / size_usd
        # Default size_usd = 5.0
        if settle_str:
            settle_pnl = float(settle_str)
            settle_px = fill + settle_pnl / 5.0
        else:
            settle_px = None
        rows.append({
            "event_type": row.get("event_type", ""),
            "match_name": row.get("match_name", ""),
            "fair_price": fp,
            "fill_price": fill,
            "settle_px": settle_px,
            "side": row.get("side", ""),
            "direction": row.get("direction", ""),
        })

print(f"\nTotal trades: {len(rows)}")
with_settle = [r for r in rows if r["settle_px"] is not None]
print(f"With settlement: {len(with_settle)}")

if not with_settle:
    print("No settlement data — can't calibrate"); sys.exit(0)

# Global bias: fair_price vs settle_px
biases = [r["fair_price"] - r["settle_px"] for r in with_settle]
fill_biases = [r["fill_price"] - r["settle_px"] for r in with_settle]

print(f"\n{'='*60}")
print("FAIR-PRICE CALIBRATION (fair_price at entry vs settlement)")
print(f"{'='*60}")
print(f"  Mean bias (fair - settle):    {statistics.mean(biases):+.4f}")
print(f"  Median bias:                  {statistics.median(biases):+.4f}")
print(f"  Stdev:                        {statistics.stdev(biases):.4f}")
print(f"  Mean |bias|:                  {statistics.mean(abs(b) for b in biases):.4f}")
print(f"  Trades where fair > settle:   {sum(1 for b in biases if b > 0)}/{len(biases)} ({sum(1 for b in biases if b > 0)/len(biases)*100:.0f}%)")
print(f"  Trades where fair < settle:   {sum(1 for b in biases if b < 0)}/{len(biases)} ({sum(1 for b in biases if b < 0)/len(biases)*100:.0f}%)")

print(f"\nFILL-PRICE vs SETTLEMENT")
print(f"  Mean (fill - settle):         {statistics.mean(fill_biases):+.4f}")
print(f"  Trades where fill > settle:   {sum(1 for b in fill_biases if b > 0)}/{len(fill_biases)} ({sum(1 for b in fill_biases if b > 0)/len(fill_biases)*100:.0f}%)")

# By event type
print(f"\n{'--- By Event Type ':─<60}")
by_evt = defaultdict(list)
for r in with_settle:
    by_evt[r["event_type"]].append(r)

print(f"  {'Event Type':<34} {'N':>4}  {'Mean(f-s)':>10}  {'Median':>8}  {'f>s%':>5}  {'Mean(fill-s)':>12}")
print(f"  {'-'*80}")
for et in sorted(by_evt, key=lambda e: -len(by_evt[e])):
    rs = by_evt[et]
    bs = [r["fair_price"] - r["settle_px"] for r in rs]
    fbs = [r["fill_price"] - r["settle_px"] for r in rs]
    n = len(bs)
    m = statistics.mean(bs)
    med = statistics.median(bs)
    pct = sum(1 for b in bs if b > 0) / n * 100
    fm = statistics.mean(fbs)
    print(f"  {et:<34} {n:>4}  {m:>+10.4f}  {med:>+8.4f}  {pct:>4.0f}%  {fm:>+12.4f}")

# By fill price bucket
print(f"\n{'--- By Fill-Price Bucket ':─<60}")
buckets = [
    ("< 0.40", lambda r: r["fill_price"] < 0.40),
    ("0.40–0.60", lambda r: 0.40 <= r["fill_price"] < 0.60),
    ("0.60–0.80", lambda r: 0.60 <= r["fill_price"] < 0.80),
    ("≥ 0.80", lambda r: r["fill_price"] >= 0.80),
]
print(f"  {'Bucket':<14} {'N':>4}  {'Mean(f-s)':>10}  {'f>s%':>5}  {'Mean(fill-s)':>12}")
for label, pred in buckets:
    rs = [r for r in with_settle if pred(r)]
    if not rs:
        continue
    bs = [r["fair_price"] - r["settle_px"] for r in rs]
    fbs = [r["fill_price"] - r["settle_px"] for r in rs]
    m = statistics.mean(bs)
    pct = sum(1 for b in bs if b > 0) / len(bs) * 100
    fm = statistics.mean(fbs)
    print(f"  {label:<14} {len(rs):>4}  {m:>+10.4f}  {pct:>4.0f}%  {fm:>+12.4f}")

# Summary verdict
print(f"\n{'='*60}")
print("VERDICT:")
mean_bias = statistics.mean(biases)
if abs(mean_bias) < 0.02:
    print(f"  Fair-price model is well-calibrated (bias {mean_bias:+.4f}). No correction needed.")
elif mean_bias > 0:
    print(f"  Fair-price model is BIASED HIGH by {mean_bias:+.4f}.")
    print(f"  The model predicts higher prices than realized settlement.")
    print(f"  This means EDGES ARE OVERSTATED — the actual alpha is less than computed.")
    print(f"  Recommendation: reduce expected_move parameters or add a {-mean_bias:.3f} calibration offset.")
else:
    print(f"  Fair-price model is BIASED LOW by {mean_bias:+.4f}.")
    print(f"  The model predicts lower prices than realized settlement.")
    print(f"  This means EDGES ARE UNDERSTATED — there's more alpha than computed.")
    print(f"  Recommendation: consider increasing expected_move or lowering MIN_EXECUTABLE_EDGE.")
