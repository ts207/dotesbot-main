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
    df_sig = pd.concat(all_df, ignore_index=True)
    
    # 2. Load markouts
    df_mark = pd.read_csv('logs/signal_markouts.csv')
    # Filter markouts to only unique timestamp+match pairs to avoid the non-unique column error
    # (Though in theory each signal firing should be unique)
    df_mark = df_mark.drop_duplicates(subset=['signal_timestamp_utc', 'match_id'])
    
    # 3. Merge on timestamp + match_id
    # We use signal_timestamp_utc from markouts which matches timestamp_utc in signals.csv
    merged = pd.merge(df_sig, df_mark[['signal_timestamp_utc', 'match_id', 'markout_30s']], 
                     left_on=['timestamp_utc', 'match_id'], 
                     right_on=['signal_timestamp_utc', 'match_id'], 
                     how='inner')
    
    # 4. Cast and Filter for Combat
    merged['steam_age_ms'] = pd.to_numeric(merged['steam_age_ms'], errors='coerce')
    merged['executable_edge'] = pd.to_numeric(merged['executable_edge'], errors='coerce')
    merged['ask'] = pd.to_numeric(merged['ask'], errors='coerce')
    merged = merged[merged['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])]
    merged = merged.dropna(subset=['markout_30s'])

    print(f"Total Combat Signals Joined for Sweep: {len(merged)}")

    def test_filter(name, col, thresholds, inverse=False):
        print(f"\n--- SWEEP: {name} ---")
        print(f"{'Threshold':<15} | {'Trades':<6} | {'WinRate':<8} | {'MeanM30':<10}")
        for t in thresholds:
            if inverse:
                subset = merged[merged[col] >= t]
            else:
                subset = merged[merged[col] <= t]
            
            if len(subset) == 0: continue
            wr = (subset['markout_30s'] > 0).mean()
            mean_m = subset['markout_30s'].mean()
            print(f"{str(t):<15} | {len(subset):<6} | {wr:.1%}   | {mean_m:+.4f}")

    # SWEEP 1: Steam Age
    test_filter("Steam Age (ms)", "steam_age_ms", [3000, 8000, 15000, 30000, 60000])

    # SWEEP 2: Fill Price
    test_filter("Fill Price", "ask", [0.75, 0.82, 0.90, 0.95, 0.99])

    # SWEEP 3: Edge
    test_filter("Executable Edge", "executable_edge", [0.10, 0.05, 0.01, 0.005, 0.003, 0.001, -0.01], inverse=True)

if __name__ == "__main__":
    run_sweep()
