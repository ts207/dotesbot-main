import pandas as pd
import numpy as np

def analyze():
    try:
        df_sig = pd.read_csv('logs/signals.csv')
        df_mark = pd.read_csv('logs/signal_markouts.csv')
        
        # combat only
        combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
        df_sig = df_sig[df_sig['event_type'].isin(combat_types)]
        
        # Rename columns to avoid confusion
        df_sig = df_sig.rename(columns={'timestamp_utc': 'signal_ts'})
        df_mark = df_mark.rename(columns={'signal_timestamp_utc': 'signal_ts'})
        
        merged = pd.merge(df_sig[['signal_ts', 'match_id', 'steam_age_ms']], 
                         df_mark[['signal_ts', 'match_id', 'markout_30s', 'event_type']], 
                         on=['signal_ts', 'match_id'], how='inner')
        
        if merged.empty:
            print("Join failed again. Checking first few timestamps...")
            print("Signals TS:", df_sig['signal_ts'].head(1).values)
            print("Markouts TS:", df_mark['signal_ts'].head(1).values)
            return

        print(f"Analyzing {len(merged)} combat signals by Steam Age...")

        # Define buckets
        buckets = [0, 3000, 5000, 8000, 15000, 30000, 1000000]
        labels = ['0-3s', '3-5s', '5-8s', '8-15s', '15-30s', '>30s']
        merged['age_bucket'] = pd.cut(merged['steam_age_ms'], bins=buckets, labels=labels)

        summary = merged.groupby('age_bucket', observed=False)['markout_30s'].agg(['count', 'mean', 'median'])
        
        # Win rate
        def win_rate(x): return (x > 0).mean()
        wr = merged.groupby('age_bucket', observed=False)['markout_30s'].apply(win_rate)
        summary['win_rate'] = wr
        
        print("\n=== Markout@30s by Steam Age Bucket ===")
        print(summary)

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    analyze()
