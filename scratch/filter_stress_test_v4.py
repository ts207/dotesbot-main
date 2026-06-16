import pandas as pd
import numpy as np

def run_sweep():
    # Load raw signal markouts - these are the 823 raw fires including ALL skips
    try:
        df = pd.read_csv('logs/signal_markouts.csv')
        df = df[df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])]
        df = df.dropna(subset=['markout_30s'])
    except Exception as e:
        print(f"Error loading markouts: {e}")
        return

    print(f"Total Raw Combat Signal Fires for Sweep: {len(df)}")

    def test_filter(name, col, thresholds, inverse=False):
        print(f"\n--- SWEEP: {name} ---")
        print(f"{'Threshold':<15} | {'Trades':<6} | {'WinRate':<8} | {'MeanM30':<10}")
        for t in thresholds:
            if inverse:
                subset = df[df[col] >= t]
            else:
                subset = df[df[col] <= t]
            
            if len(subset) == 0: continue
            wr = (subset['markout_30s'] > 0).mean()
            mean_m = subset['markout_30s'].mean()
            print(f"{str(t):<15} | {len(subset):<6} | {wr:.1%}   | {mean_m:+.4f}")

    # SWEEP 1: Fill Price (reference_ask is the price at signal time)
    test_filter("Fill Price", "reference_ask", [0.75, 0.82, 0.85, 0.90, 0.95, 0.98])

    # SWEEP 2: Edge (executable_edge is in the markout log)
    # Note: executable_edge in markout log is only populated for things that had a book.
    test_filter("Executable Edge", "executable_edge", [0.10, 0.05, 0.02, 0.01, 0.005, 0.001], inverse=True)

    # SWEEP 3: Combined (The current portfolio rules)
    print("\n--- TEST: Portfolio Logic ---")
    current = df[(df['reference_ask'] <= 0.90) & (df['executable_edge'] >= 0.003)]
    wr = (current['markout_30s'] > 0).mean()
    mean_m = current['markout_30s'].mean()
    print(f"15s/90c/0.003 Edge | Trades: {len(current):<6} | WinRate: {wr:.1%} | Mean: {mean_m:+.4f}")

run_sweep()
