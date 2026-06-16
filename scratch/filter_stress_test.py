import pandas as pd
import glob
import numpy as np

def run_sweep():
    # 1. Load consolidated data
    sig_files = ['logs/signals.csv'] + glob.glob('logs/signals.csv*.bak')
    all_df = []
    cols = pd.read_csv('logs/signals.csv', nrows=0).columns
    for f in sig_files:
        try:
            df = pd.read_csv(f, names=cols, on_bad_lines='skip') if 'bak' in f else pd.read_csv(f, on_bad_lines='skip')
            all_df.append(df)
        except: pass
    df = pd.concat(all_df, ignore_index=True)
    
    # Load markouts to see if relaxed trades are actually profitable
    df_mark = pd.read_csv('logs/signal_markouts.csv')
    df_mark = df_mark.rename(columns={'signal_timestamp_utc': 'timestamp_utc'})
    
    # Merge
    merged = pd.merge(df, df_mark[['timestamp_utc', 'match_id', 'markout_30s']], 
                     on=['timestamp_utc', 'match_id'], how='inner')
    
    # Cast
    merged['steam_age_ms'] = pd.to_numeric(merged['steam_age_ms'], errors='coerce')
    merged['executable_edge'] = pd.to_numeric(merged['executable_edge'], errors='coerce')
    merged['ask'] = pd.to_numeric(merged['ask'], errors='coerce')
    merged = merged[merged['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])]

    print(f"Total Combined Combat Signals for Sweep: {len(merged)}")

    def test_filter(name, col, thresholds):
        print(f"\n--- SWEEP: {name} ---")
        print(f"{'Threshold':<15} | {'Trades':<6} | {'WinRate':<8} | {'MeanM30':<10}")
        for t in thresholds:
            # We assume OTHER filters are relaxed for this specific test to see raw potential
            subset = merged[merged[col] <= t]
            if len(subset) == 0: continue
            wr = (subset['markout_30s'] > 0).mean()
            mean_m = subset['markout_30s'].mean()
            print(f"{t:<15} | {len(subset):<6} | {wr:.1%}   | {mean_m:+.4f}")

    # SWEEP 1: Steam Age (How old is too old?)
    test_filter("Steam Age (ms)", "steam_age_ms", [3000, 5000, 8000, 15000, 30000, 60000])

    # SWEEP 2: Fill Price (How expensive is too expensive?)
    test_filter("Fill Price", "ask", [0.75, 0.82, 0.85, 0.90, 0.95, 0.98])

    # SWEEP 3: Edge (Inverse: executable_edge >= t, but we'll use a filter)
    print("\n--- SWEEP: Executable Edge (>= T) ---")
    print(f"{'Threshold':<15} | {'Trades':<6} | {'WinRate':<8} | {'MeanM30':<10}")
    for t in [0.05, 0.03, 0.01, 0.005, 0.003, 0.001]:
        subset = merged[merged['executable_edge'] >= t]
        if len(subset) == 0: continue
        wr = (subset['markout_30s'] > 0).mean()
        mean_m = subset['markout_30s'].mean()
        print(f"{t:<15} | {len(subset):<6} | {wr:.1%}   | {mean_m:+.4f}")

run_sweep()
