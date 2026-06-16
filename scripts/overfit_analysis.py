"""Overfit analysis for the SIM500 bankroll strategy.

Sweeps thresholds and performs bootstrap resampling to check for robustness.
"""
from __future__ import annotations

import sys
from pathlib import Path
from statistics import mean, stdev
import random

ROOT = Path('/home/tstuv/dota-poly-signal-pnl-asd')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

from backtest_buy_both_scalp import _load_markets, _load_match_windows, simulate_one  # noqa: E402

SLIPPAGE = 0.04
START_BANKROLL = 500.0

def get_results():
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
    return results

def run_sim(results, skew_limit, sum_limit, stake_usd):
    bankroll_A = START_BANKROLL
    bankroll_B = START_BANKROLL
    bankroll_C = START_BANKROLL
    trades = 0
    pnls_A = []
    pnls_B = []
    pnls_C = []
    
    TRAILING_STOP = 0.03
    
    for r in results:
        if r['skew'] > skew_limit or r['sum'] > sum_limit:
            continue
        
        # Strategy A: Hold to Settle
        pnl_A = (r['pnl_settle_hold'] - SLIPPAGE) * stake_usd
        
        # Strategy B: Perfect Peak (Foresight)
        pnl_B = (r['pnl_scratch_and_ride_peak'] - SLIPPAGE) * stake_usd
        
        # Strategy C: Trailing Stop (Live Realistic)
        # Re-derive from Strategy B but subtract the trailing stop distance from the winner
        # Strategy B PnL = (Scratch_Px + Peak_Px)*(1-Fee) - Cost
        # We assume the 'Ride' side gets (Peak - Trailing Stop) instead of Peak.
        # This is an approximation: (Peak - 0.03) * (1-Fee)
        # Loss vs Strategy B is approx 0.03 * Stake
        pnl_C = pnl_B - (TRAILING_STOP * stake_usd)
        
        bankroll_A += pnl_A
        bankroll_B += pnl_B
        bankroll_C += pnl_C
        trades += 1
        pnls_A.append(pnl_A)
        pnls_B.append(pnl_B)
        pnls_C.append(pnl_C)
    
    if trades == 0:
        return 0, 0, 0, 0, 0, 0, 0
    return trades, bankroll_A - START_BANKROLL, mean(pnls_A), bankroll_B - START_BANKROLL, mean(pnls_B), bankroll_C - START_BANKROLL, mean(pnls_C)

def parameter_sweep(results):
    print("=== PARAMETER SWEEP: SKEW vs SUM (Stake $50) ===")
    print(f"{'Skew':>6} | {'Sum':>6} | {'Tr':>3} | {'Net P&L A':>10} | {'Net P&L B':>10} | {'Net P&L C':>10}")
    print("-" * 75)
    for skew in [0.04, 0.08, 0.12, 0.15]:
        for s_sum in [1.02, 1.03, 1.05]:
            t, pnlA, avgA, pnlB, avgB, pnlC, avgC = run_sim(results, skew, s_sum, 50.0)
            if t > 0:
                print(f"{skew:6.2f} | {s_sum:6.2f} | {t:3} | ${pnlA:9.2f} | ${pnlB:9.2f} | ${pnlC:9.2f}")

def bootstrap_analysis(results, skew_limit, sum_limit, stake_usd, iterations=1000):
    print(f"\n=== BOOTSTRAP ANALYSIS (Skew <= {skew_limit}, Sum <= {sum_limit}, Stake ${stake_usd}) ===")
    filtered = [r for r in results if r['skew'] <= skew_limit and r['sum'] <= sum_limit]
    if not filtered:
        print("No matches pass the filter.")
        return
    
    sample_pnls = [(r['pnl_scratch_and_ride_peak'] - SLIPPAGE) * stake_usd for r in filtered]
    n = len(sample_pnls)
    
    bootstrap_results = []
    for _ in range(iterations):
        resample = [random.choice(sample_pnls) for _ in range(n)]
        bootstrap_results.append(sum(resample))
    
    bootstrap_results.sort()
    p5 = bootstrap_results[int(iterations * 0.05)]
    p50 = bootstrap_results[int(iterations * 0.50)]
    p95 = bootstrap_results[int(iterations * 0.95)]
    
    print(f"Sample size: {n} matches")
    print(f"Original Net P&L: ${sum(sample_pnls):.2f}")
    print(f"Bootstrap 5th percentile:  ${p5:.2f}")
    print(f"Bootstrap 50th percentile: ${p50:.2f}")
    print(f"Bootstrap 95th percentile: ${p95:.2f}")
    
    prob_loss = sum(1 for p in bootstrap_results if p < 0) / iterations
    print(f"Probability of loss (resampled): {prob_loss:.1%}")

def time_decay_check(results):
    print("\n=== TIME DECAY / REGIME CHECK ===")
    # Split results into first half and second half
    n = len(results)
    if n < 4:
        return
    first_half = results[:n//2]
    second_half = results[n//2:]
    
    def report_half(label, res):
        t, pnlA, avgA, pnlB, avgB, pnlC, avgC = run_sim(res, 0.08, 1.03, 50.0)
        print(f"{label}: {t} trades, Avg A: ${avgA:.2f}, Avg B: ${avgB:.2f}, Avg C: ${avgC:.2f}")
    
    report_half("First Half", first_half)
    report_half("Second Half", second_half)

if __name__ == "__main__":
    results = get_results()
    print(f"Total simulated matches: {len(results)}")
    parameter_sweep(results)
    bootstrap_analysis(results, 0.08, 1.03, 50.0)
    time_decay_check(results)
