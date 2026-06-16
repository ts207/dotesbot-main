"""Simulate the $500 bankroll with backtest data at varying per-pair sizes.

For each match in chronological order, apply the strategy-B PnL scaled by
stake size. Tracks bankroll trajectory, max drawdown, worst day, etc.
"""
from __future__ import annotations

import sys
from pathlib import Path
from statistics import mean, stdev

ROOT = Path('/home/tstuv/dota-poly-signal-pnl-asd')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

from backtest_buy_both_scalp import _load_markets, _load_match_windows, simulate_one  # noqa: E402

SLIPPAGE = 0.04   # 2c on each side fill, baked into per-$1 PnL
markets = _load_markets()
windows = _load_match_windows()

results = []
for mid, (t0, tN, rl) in windows.items():
    if mid not in markets:
        continue
    r = simulate_one(mid, markets[mid], t0, tN, rl)
    if r:
        r['t0'] = t0
        r['skew'] = abs(r['yes_entry'] - r['no_entry'])
        r['sum'] = r['yes_entry'] + r['no_entry']
        results.append(r)
results.sort(key=lambda r: r['t0'])

def simulate(label, results, stake_usd, *, filter_fn=None, frac=None, start=500.0,
             pnl_key='pnl_scratch_and_ride_peak'):
    """If frac is given, stake = max($5, frac × bankroll). Otherwise fixed stake_usd."""
    bankroll = start
    peak = bankroll
    max_dd = 0.0
    trades = 0
    wins = 0
    pnls = []
    skipped = 0
    for r in results:
        if filter_fn and not filter_fn(r):
            skipped += 1
            continue
        stake = max(5.0, bankroll * frac) if frac is not None else stake_usd
        cost = stake * 1.02
        if cost > bankroll:
            continue
        pnl_dollar = (r[pnl_key] - SLIPPAGE) * stake
        bankroll += pnl_dollar
        trades += 1
        pnls.append(pnl_dollar)
        if pnl_dollar > 0:
            wins += 1
        if bankroll > peak:
            peak = bankroll
        dd = peak - bankroll
        if dd > max_dd:
            max_dd = dd
    if trades == 0:
        print(f"{label}: 0 trades")
        return
    print(f"\n--- {label} ---")
    print(f"  trades:        {trades}/{trades+skipped}")
    print(f"  final $:       ${bankroll:.2f}  (start ${start:.0f})")
    print(f"  net P&L:       ${bankroll-start:+.2f}  ({(bankroll-start)/start*100:+.1f}%)")
    print(f"  win rate:      {wins/trades*100:.0f}%")
    print(f"  avg/trade:     ${mean(pnls):+.2f}")
    if trades >= 2: print(f"  stdev/trade:   ${stdev(pnls):.2f}")
    print(f"  best trade:    ${max(pnls):+.2f}")
    print(f"  worst trade:   ${min(pnls):+.2f}")
    print(f"  peak bankroll: ${peak:.2f}")
    print(f"  max drawdown:  ${max_dd:.2f}  ({max_dd/peak*100:.0f}%)")

print(f"=== Sample: {len(results)} matches over 11 days ===")
print(f"=== Slippage assumed: -${SLIPPAGE}/pair (-4% drag) ===\n")

# No filter — take every match
for stake in [25, 50, 75, 100]:
    simulate("NO FILTER", results, stake)

# Filtered: |skew| ≤ 0.08 AND sum ≤ 1.03
def filt(r):
    return r['skew'] <= 0.08 and r['sum'] <= 1.03

print("\n" + "="*50)
print("WITH ENTRY FILTER (skew ≤ 0.08, sum ≤ 1.03)")
print("="*50)
for stake in [25, 50, 75, 100]:
    simulate("FILTERED", results, stake, filter_fn=filt)

print("\n" + "="*60)
print("COMPOUNDING SIZING — stake = X% of current bankroll")
print("="*60)
print("\n>>> NO FILTER, compounding")
for frac in [0.05, 0.10, 0.15, 0.20, 0.25]:
    simulate(f"NO FILTER  {frac*100:.0f}% of bankroll", results, 0, frac=frac)

print("\n>>> FILTERED, compounding")
for frac in [0.05, 0.10, 0.15, 0.20, 0.25, 0.33, 0.50]:
    simulate(f"FILTERED   {frac*100:.0f}% of bankroll", results, 0, filter_fn=filt, frac=frac)

print("\n" + "="*60)
print("STRATEGY A — scratch + HOLD loser to settle (no peak ride)")
print("="*60)
print("\n>>> NO FILTER, fixed sizing")
for stake in [25, 50, 75, 100]:
    simulate(f"A NO FILTER ${stake}", results, stake, pnl_key='pnl_settle_hold')
print("\n>>> FILTERED, fixed sizing")
for stake in [25, 50, 75, 100]:
    simulate(f"A FILTERED ${stake}", results, stake, filter_fn=filt, pnl_key='pnl_settle_hold')
print("\n>>> NO FILTER, compounding")
for frac in [0.05, 0.10, 0.15, 0.20, 0.25]:
    simulate(f"A NO FILTER {frac*100:.0f}%", results, 0, frac=frac, pnl_key='pnl_settle_hold')
print("\n>>> FILTERED, compounding")
for frac in [0.05, 0.10, 0.15, 0.20, 0.25, 0.33, 0.50]:
    simulate(f"A FILTERED {frac*100:.0f}%", results, 0, filter_fn=filt, frac=frac, pnl_key='pnl_settle_hold')
