"""Analyze the LoL book data collected so far.

Reports:
  1. Tick volume per market (which are active)
  2. Latest bid/ask per market (current prices)
  3. Spread distribution (would scalp/event filters pass?)
  4. Scalp filter qualification (skew + sum) for each game's YES/NO pair
  5. Liquidity check (bid_size / ask_size in dollars)
"""
from __future__ import annotations
import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median
import yaml

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT / "logs" / "lol_book_events.csv"
YAML_FILE = ROOT / "lol_markets.yaml"


def fnum(s):
    try: return float(s) if s not in (None, "") else None
    except: return None


# Load markets → token → market mapping
data = yaml.safe_load(YAML_FILE.read_text()) or {}
markets = data.get("markets", [])
print(f"=== LoL DATA ANALYSIS ===")
print(f"Markets tracked: {len(markets)}")

token_to_market = {}
for m in markets:
    yt = str(m.get("yes_token_id") or ""); nt = str(m.get("no_token_id") or "")
    if yt: token_to_market[yt] = {"market_id": m["market_id"], "side": "YES",
                                    "question": m["question"], "type": m.get("market_type", "?")}
    if nt: token_to_market[nt] = {"market_id": m["market_id"], "side": "NO",
                                    "question": m["question"], "type": m.get("market_type", "?")}

# Stream book events
last_book: dict[str, dict] = {}   # token -> latest row
tick_count: dict[str, int] = defaultdict(int)
all_spreads = []
ticks_with_both = 0
total_ticks = 0
first_ts = last_ts = None

with BOOK.open() as f:
    for row in csv.DictReader(f):
        total_ticks += 1
        aid = row["asset_id"]
        bid = fnum(row["best_bid"]); ask = fnum(row["best_ask"])
        bs = fnum(row["bid_size"]); as_ = fnum(row["ask_size"])
        ts = row["timestamp_utc"]
        if first_ts is None: first_ts = ts
        last_ts = ts
        tick_count[aid] += 1
        # Merge bid/ask updates that arrive separately
        prev = last_book.get(aid, {})
        if bid is not None: prev["bid"] = bid; prev["bid_size"] = bs
        if ask is not None: prev["ask"] = ask; prev["ask_size"] = as_
        prev["ts"] = ts
        last_book[aid] = prev
        if prev.get("bid") is not None and prev.get("ask") is not None:
            ticks_with_both += 1
            all_spreads.append(prev["ask"] - prev["bid"])

print(f"Time window:     {first_ts} → {last_ts}")
print(f"Total ticks:     {total_ticks}")
print(f"With both sides: {ticks_with_both}")
print(f"Unique tokens:   {len(tick_count)}")
print()

# Activity per market (sum YES + NO tick counts)
print("="*100)
print("MARKET ACTIVITY (ticks observed, current top-of-book)")
print("="*100)
print(f"{'question':<70s} {'side':>5s} {'ticks':>5s} {'bid':>6s} {'ask':>6s} {'spread':>6s} {'bid$':>8s} {'ask$':>8s}")
seen_markets: set[str] = set()
rows = []
for aid, n in sorted(tick_count.items(), key=lambda x: -x[1]):
    m = token_to_market.get(aid)
    if not m: continue
    lb = last_book.get(aid, {})
    bid = lb.get("bid"); ask = lb.get("ask")
    sp = (ask - bid) if (bid is not None and ask is not None) else None
    bs = lb.get("bid_size") or 0; as_ = lb.get("ask_size") or 0
    rows.append({
        "question": m["question"][:68], "side": m["side"], "n": n,
        "bid": bid, "ask": ask, "sp": sp,
        "bid_usd": bs * (bid or 0), "ask_usd": as_ * (ask or 0),
        "market_id": m["market_id"],
    })

for r in rows[:30]:
    bid_s = f"{r['bid']:.3f}" if r['bid'] is not None else "   .  "
    ask_s = f"{r['ask']:.3f}" if r['ask'] is not None else "   .  "
    sp_s = f"{r['sp']:.3f}" if r['sp'] is not None else "  .  "
    print(f"{r['question']:<70s} {r['side']:>5s} {r['n']:>5d}  "
          f"{bid_s:>6}  {ask_s:>6}  {sp_s:>6}  "
          f"${r['bid_usd']:>6.0f}  ${r['ask_usd']:>6.0f}")
if len(rows) > 30:
    print(f"  ... ({len(rows)-30} more)")
print()

# Spread distribution
if all_spreads:
    all_spreads.sort()
    print("="*100)
    print(f"SPREAD DISTRIBUTION (n={len(all_spreads)})")
    print("="*100)
    print(f"  min:    {min(all_spreads):.3f}")
    print(f"  median: {median(all_spreads):.3f}")
    print(f"  p25:    {all_spreads[len(all_spreads)//4]:.3f}")
    print(f"  p75:    {all_spreads[3*len(all_spreads)//4]:.3f}")
    print(f"  p95:    {all_spreads[int(0.95*len(all_spreads))]:.3f}")
    print(f"  max:    {max(all_spreads):.3f}")
    print()
    buckets = [(0, 0.02), (0.02, 0.04), (0.04, 0.07), (0.07, 0.15), (0.15, 1.0)]
    print(f"  {'bucket':>14}  {'n':>4}  {'%':>5}")
    for lo, hi in buckets:
        c = sum(1 for s in all_spreads if lo <= s < hi)
        print(f"  [{lo:.2f},{hi:.2f})    {c:>4}  {c/len(all_spreads)*100:>4.0f}%")

# SCALP filter — pair YES + NO for each market, check current ask values
print()
print("="*100)
print("SCALP FILTER CHECK — current YES_ask + NO_ask per market")
print("="*100)
print(f"  Filter: |yes_ask - no_ask| ≤ 0.08 AND yes_ask + no_ask ≤ 1.03")
print(f"          AND 0.40 ≤ both asks ≤ 0.60")
print()
print(f"  {'question':<68s} {'yes_ask':>7s} {'no_ask':>6s} {'skew':>5s} {'sum':>5s} {'verdict':>9s}")
n_qualify = 0
for m in markets:
    yt = str(m.get("yes_token_id") or ""); nt = str(m.get("no_token_id") or "")
    ya = last_book.get(yt, {}).get("ask"); na = last_book.get(nt, {}).get("ask")
    if ya is None or na is None: continue
    skew = abs(ya - na); s_sum = ya + na
    qualifies = (skew <= 0.08 and s_sum <= 1.03 and 0.40 <= ya <= 0.60 and 0.40 <= na <= 0.60)
    if qualifies: n_qualify += 1
    verdict = "✓ scalp" if qualifies else "—"
    print(f"  {m['question'][:66]:<68s} {ya:>7.3f} {na:>6.3f} {skew:>5.3f} {s_sum:>5.3f} {verdict:>9s}")
print(f"\n  Qualifying for scalp: {n_qualify}/{sum(1 for m in markets if str(m.get('yes_token_id','')) in last_book and str(m.get('no_token_id','')) in last_book)}")

# Liquidity check
print()
print("="*100)
print("LIQUIDITY DEPTH (top-of-book size in $ at current bid/ask)")
print("="*100)
sizes = []
for aid, lb in last_book.items():
    bid = lb.get("bid"); ask = lb.get("ask")
    bs = lb.get("bid_size") or 0; as_ = lb.get("ask_size") or 0
    if bid is not None and bs > 0: sizes.append(("bid", bs * bid))
    if ask is not None and as_ > 0: sizes.append(("ask", as_ * ask))
if sizes:
    bid_sizes = sorted([v for s, v in sizes if s == "bid"])
    ask_sizes = sorted([v for s, v in sizes if s == "ask"])
    print(f"  bid$:  n={len(bid_sizes)}  min=${min(bid_sizes):.0f}  med=${median(bid_sizes):.0f}  max=${max(bid_sizes):.0f}")
    print(f"  ask$:  n={len(ask_sizes)}  min=${min(ask_sizes):.0f}  med=${median(ask_sizes):.0f}  max=${max(ask_sizes):.0f}")
    print()
    # How many would accept a $50 trade without sweeping?
    bid50 = sum(1 for v in bid_sizes if v >= 50)
    ask50 = sum(1 for v in ask_sizes if v >= 50)
    print(f"  Books with bid_$ ≥ $50:  {bid50}/{len(bid_sizes)} ({bid50/len(bid_sizes)*100:.0f}%)")
    print(f"  Books with ask_$ ≥ $50:  {ask50}/{len(ask_sizes)} ({ask50/len(ask_sizes)*100:.0f}%)")
