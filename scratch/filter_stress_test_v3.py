import pandas as pd
import numpy as np

def run_sweep():
    # 1. Use shadow_trades.csv as the base because it already contains:
    # - event_type
    # - entry_price (ask)
    # - lag (steam_age)
    # - markout_30s
    # - executable_edge
    try:
        df = pd.read_csv('logs/shadow_trades.csv')
        df = df[df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])]
        df = df.dropna(subset=['markout_30s'])
    except Exception as e:
        print(f"Error loading shadow trades: {e}")
        return

    print(f"Total Historical Shadow Combat Signals: {len(df)}")

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

    # SWEEP 1: Steam Age (lag is in seconds in shadow_trades)
    test_filter("Steam Age (s)", "lag", [3, 5, 8, 15, 30, 60])

    # SWEEP 2: Fill Price
    test_filter("Fill Price", "entry_price", [0.75, 0.82, 0.85, 0.90, 0.95, 0.98])

    # SWEEP 3: Edge
    test_filter("Executable Edge", "executable_edge", [0.10, 0.05, 0.02, 0.01, 0.005, 0.001], inverse=True)

if __name__ == "__main__":
    run_sweep()
