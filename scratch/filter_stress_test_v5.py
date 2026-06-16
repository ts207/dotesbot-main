import pandas as pd
import numpy as np

def run_sweep():
    # Load all markouts - these represent EVERY signal fire, regardless of filters
    try:
        df = pd.read_csv('logs/signal_markouts.csv')
        # Filter for combat only
        df = df[df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])]
        df = df.dropna(subset=['markout_30s'])
    except Exception as e:
        print(f"Error: {e}")
        return

    print(f"Auditing {len(df)} Total Combat Signal Fires...")

    # SWEEP: Minimum Executable Edge (>= T)
    print("\n--- Filter Sweep: Executable Edge ---")
    print(f"{'Edge Threshold':<15} | {'Trades':<6} | {'WinRate':<8} | {'Mean Alpha':<10}")
    for t in [0.05, 0.02, 0.01, 0.005, 0.003, 0.001, -0.01]:
        subset = df[df['executable_edge'] >= t]
        if len(subset) == 0: continue
        wr = (subset['markout_30s'] > 0).mean()
        mean_m = subset['markout_30s'].mean()
        print(f"{t:<15} | {len(subset):<6} | {wr:.1%}   | {mean_m:+.4f}")

    # SWEEP: Fill Price (<= T)
    print("\n--- Filter Sweep: Fill Price Cap ---")
    print(f"{'Price Cap':<15} | {'Trades':<6} | {'WinRate':<8} | {'Mean Alpha':<10}")
    for t in [0.75, 0.82, 0.85, 0.90, 0.95, 0.98]:
        subset = df[df['reference_ask'] <= t]
        if len(subset) == 0: continue
        wr = (subset['markout_30s'] > 0).mean()
        mean_m = subset['markout_30s'].mean()
        print(f"{t:<15} | {len(subset):<6} | {wr:.1%}   | {mean_m:+.4f}")

    # OPTIMIZATION: Final Gold Standard Logic
    print("\n--- Final Strategy Comparison ---")
    # Current "Strict" Logic
    strict = df[(df['reference_ask'] <= 0.82) & (df['executable_edge'] >= 0.005)]
    # New "Optimized" Logic (Relaxed Edge, Relaxed Cap)
    optimized = df[(df['reference_ask'] <= 0.90) & (df['executable_edge'] >= 0.001)]
    
    for label, subset in [("Strict (0.82c/0.005e)", strict), ("Optimized (0.90c/0.001e)", optimized)]:
        print(f"{label:25} | Trades: {len(subset):<3} | WinRate: {(subset['markout_30s']>0).mean():.1%} | Total Alpha: {subset['markout_30s'].sum():+.4f}")

run_sweep()
