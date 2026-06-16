import pandas as pd
import glob
import numpy as np

def analyze():
    # 1. Load and aggregate ALL signal logs
    sig_files = ['logs/signals.csv'] + glob.glob('logs/signals.csv*.bak')
    all_sigs = []
    for f in sig_files:
        try:
            if 'bak' in f:
                # bak files are headerless, use headers from primary
                df = pd.read_csv(f, names=pd.read_csv('logs/signals.csv', nrows=0).columns)
            else:
                df = pd.read_csv(f)
            all_sigs.append(df)
        except: pass
    
    df_sig = pd.concat(all_sigs, ignore_index=True)
    df_mark = pd.read_csv('logs/signal_markouts.csv')
    
    # combat only
    combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
    df_sig = df_sig[df_sig['event_type'].isin(combat_types)]
    
    # Rename for merge
    df_sig = df_sig.rename(columns={'timestamp_utc': 'signal_ts'})
    df_mark = df_mark.rename(columns={'signal_timestamp_utc': 'signal_ts'})
    
    # Round to 100ms for safety in case of float precision/string issues
    # But strings should match exactly if they were written by the same logic
    merged = pd.merge(df_sig[['signal_ts', 'match_id', 'steam_age_ms']], 
                     df_mark[['signal_ts', 'match_id', 'markout_30s', 'event_type']], 
                     on=['signal_ts', 'match_id'], how='inner')
    
    if merged.empty:
        print("Join failed.")
        return

    print(f"Analyzing {len(merged)} combat signals by Steam Age...")

    # Define buckets
    buckets = [0, 3000, 5000, 8000, 15000, 30000, 1000000]
    labels = ['0-3s', '3-5s', '5-8s', '8-15s', '15-30s', '>30s']
    merged['age_bucket'] = pd.cut(merged['steam_age_ms'], bins=buckets, labels=labels)

    summary = merged.groupby('age_bucket', observed=False)['markout_30s'].agg(['count', 'mean', 'median'])
    def win_rate(x): return (x > 0).mean()
    summary['win_rate'] = merged.groupby('age_bucket', observed=False)['markout_30s'].apply(win_rate)
    
    print("\n=== Markout@30s by Steam Age Bucket ===")
    print(summary)

if __name__ == "__main__":
    analyze()
