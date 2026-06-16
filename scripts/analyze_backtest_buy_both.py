"""Deep analysis of the buy-both-scalp backtest results.

Re-runs the simulation (caching) and breaks PnL down across multiple
dimensions: league, entry skew, entry sum, peak gap, win-side, scratch
pattern, etc. Identifies the strongest signal for filtering bad trades.
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, median, stdev

import yaml

ROOT = Path('/home/tstuv/dota-poly-signal-pnl-asd')
sys.path.insert(0, str(ROOT))

# Re-import the sim from the existing backtest script
sys.path.insert(0, str(ROOT / 'scripts'))
from backtest_buy_both_scalp import _load_markets, _load_match_windows, simulate_one  # noqa: E402

# League lookup from raw_snapshots
import glob
match_league = {}
for p in [ROOT / 'logs' / 'raw_snapshots.csv'] + list(map(Path, sorted(glob.glob(str(ROOT / 'logs' / 'raw_snapshots.csv.*.bak'))))):
    try:
        with p.open() as f:
            for row in csv.DictReader(f):
                mid = row.get('match_id', '').strip()
                lid = row.get('league_id', '').strip()
                if mid and lid:
                    match_league.setdefault(mid, lid)
    except OSError: pass

LEAGUE_NAMES = {
    '19696': 'DreamLeague',
    '19101': 'BLAST Slam',
    '19742': 'Winline Star',
    '17924': 'misc',
    '18867': 'misc',
    '18959': 'misc',
}

markets = _load_markets()
windows = _load_match_windows()
results = []
for mid, (t0, tN, rl) in windows.items():
    if mid not in markets:
        continue
    r = simulate_one(mid, markets[mid], t0, tN, rl)
    if r:
        r['league'] = LEAGUE_NAMES.get(match_league.get(mid, ''), match_league.get(mid, '?'))
        r['entry_skew'] = abs(r['yes_entry'] - r['no_entry'])
        r['entry_sum'] = r['yes_entry'] + r['no_entry']
        r['both_scratched'] = (r['yes_scratched_at'] is not None and r['no_scratched_at'] is not None)
        r['peak_max'] = max(r['yes_peak'], r['no_peak'])
        results.append(r)

n = len(results)
print(f"=== Sample: {n} matches ===\n")

def bucket_stats(label: str, key, edges: list[float]) -> None:
    print(f"\n--- by {label} ---")
    buckets = defaultdict(list)
    for r in results:
        v = key(r)
        for i, e in enumerate(edges):
            if v <= e:
                buckets[(i, f"≤{e}")].append(r)
                break
        else:
            buckets[(len(edges), f">{edges[-1]}")].append(r)
    print(f"  {'bucket':>14}  {'n':>3}  {'avg_B':>8}  {'med_B':>8}  {'win%':>5}  {'worst':>7}")
    for (i, lbl), rs in sorted(buckets.items()):
        if not rs: continue
        pnls = [r['pnl_scratch_and_ride_peak'] for r in rs]
        wins = sum(1 for v in pnls if v > 0)
        print(f"  {lbl:>14}  {len(rs):>3}  {mean(pnls):+8.3f}  {median(pnls):+8.3f}  {wins/len(rs)*100:>4.0f}%  {min(pnls):+7.3f}")


# Whole-sample stats
all_b = [r['pnl_scratch_and_ride_peak'] for r in results]
all_a = [r['pnl_settle_hold'] for r in results]
wins_b = sum(1 for v in all_b if v > 0)
print(f"Strategy A: avg={mean(all_a):+.3f}  med={median(all_a):+.3f}  win%={sum(1 for v in all_a if v>0)/n*100:.0f}%  total={sum(all_a):+.2f}")
print(f"Strategy B: avg={mean(all_b):+.3f}  med={median(all_b):+.3f}  win%={wins_b/n*100:.0f}%  total={sum(all_b):+.2f}")
print(f"  stdev={stdev(all_b):.3f}  best={max(all_b):+.3f}  worst={min(all_b):+.3f}")

# League breakdown
print("\n--- by league ---")
print(f"  {'league':>14}  {'n':>3}  {'avg_B':>8}  {'med_B':>8}  {'win%':>5}")
by_league = defaultdict(list)
for r in results: by_league[r['league']].append(r)
for league, rs in sorted(by_league.items(), key=lambda x: -len(x[1])):
    pnls = [r['pnl_scratch_and_ride_peak'] for r in rs]
    print(f"  {league:>14}  {len(rs):>3}  {mean(pnls):+8.3f}  {median(pnls):+8.3f}  {sum(1 for v in pnls if v>0)/len(rs)*100:>4.0f}%")

# Win-side breakdown (does the strategy work better when YES wins or NO?)
print("\n--- by which side won ---")
print(f"  {'side':>14}  {'n':>3}  {'avg_B':>8}  {'med_B':>8}  {'win%':>5}")
for label, key in [('YES won', lambda r: r['yes_wins']), ('NO won', lambda r: not r['yes_wins'])]:
    rs = [r for r in results if key(r)]
    if rs:
        pnls = [r['pnl_scratch_and_ride_peak'] for r in rs]
        print(f"  {label:>14}  {len(rs):>3}  {mean(pnls):+8.3f}  {median(pnls):+8.3f}  {sum(1 for v in pnls if v>0)/len(rs)*100:>4.0f}%")

# Bucket: entry skew
bucket_stats("entry_skew (|YESe-NOe|)", lambda r: r['entry_skew'], [0.04, 0.08, 0.15])

# Bucket: entry sum (proxy for spread + fees baked in)
bucket_stats("entry_sum (YESe+NOe)", lambda r: r['entry_sum'], [0.99, 1.01, 1.03])

# Bucket: did both sides scratch?
print("\n--- by scratch outcome ---")
print(f"  {'pattern':>20}  {'n':>3}  {'avg_B':>8}  {'med_B':>8}  {'win%':>5}")
patterns = {
    'both scratched': lambda r: r['yes_scratched_at'] is not None and r['no_scratched_at'] is not None,
    'only YES scratched': lambda r: r['yes_scratched_at'] is not None and r['no_scratched_at'] is None,
    'only NO scratched': lambda r: r['yes_scratched_at'] is None and r['no_scratched_at'] is not None,
    'neither scratched': lambda r: r['yes_scratched_at'] is None and r['no_scratched_at'] is None,
}
for label, key in patterns.items():
    rs = [r for r in results if key(r)]
    if rs:
        pnls = [r['pnl_scratch_and_ride_peak'] for r in rs]
        print(f"  {label:>20}  {len(rs):>3}  {mean(pnls):+8.3f}  {median(pnls):+8.3f}  {sum(1 for v in pnls if v>0)/len(rs)*100:>4.0f}%")

# Combined filter: entry_skew ≤ 0.08 AND entry_sum ≤ 1.03
print("\n--- with combined entry filter (skew ≤ 0.08 AND sum ≤ 1.03) ---")
filt = [r for r in results if r['entry_skew'] <= 0.08 and r['entry_sum'] <= 1.03]
if filt:
    pnls = [r['pnl_scratch_and_ride_peak'] for r in filt]
    print(f"  n={len(filt)}/{n}  avg={mean(pnls):+.3f}  med={median(pnls):+.3f}  win%={sum(1 for v in pnls if v>0)/len(filt)*100:.0f}%  total={sum(pnls):+.2f}")
    print(f"  stdev={stdev(pnls):.3f}  best={max(pnls):+.3f}  worst={min(pnls):+.3f}")

# Sharpe-like ratio for both strategies (if any losing trades)
def sharpe(vs):
    if len(vs) < 3 or stdev(vs) == 0: return None
    return mean(vs) / stdev(vs)

print(f"\nSharpe-ish (avg/stdev) — B raw: {sharpe(all_b):.2f}")
if filt:
    print(f"Sharpe-ish (avg/stdev) — B filtered: {sharpe([r['pnl_scratch_and_ride_peak'] for r in filt]):.2f}")
