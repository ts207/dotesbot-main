import pandas as pd
import numpy as np

def analyze():
    try:
        # We need both signals.csv (for steam_age_ms) and signal_markouts.csv (for outcomes)
        # Note: signal_markouts.csv might not have steam_age_ms directly, let's check.
        # Actually, signals.csv has everything we need if markouts were logged there, 
        # but markouts are in signal_markouts.csv. 
        # Let's try to join them on match_id and timestamp_utc.
        
        df_sig = pd.read_csv('logs/signals.csv')
        df_mark = pd.read_csv('logs/signal_markouts.csv')
        
        # combat only
        combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
        df_sig = df_sig[df_sig['event_type'].isin(combat_types)]
        
        # Merge to get steam_age and markouts in one place
        # Using a fuzzy merge or rounding timestamps might be needed if they differ by ms
        df_sig['ts_round'] = pd.to_datetime(df_sig['timestamp_utc']).dt.round('S')
        df_mark['ts_round'] = pd.to_datetime(df_mark['timestamp_utc']).dt.round('S')
        
        merged = pd.merge(df_sig, df_mark[['ts_round', 'match_id', 'markout_30s']], 
                         on=['ts_round', 'match_id'], how='inner')
        
        if merged.empty:
            print("Could not join signals and markouts for staleness analysis.")
            # Fallback: check if signals.csv has steam_age_ms and if we can use it
            return

        print(f"Analyzing {len(merged)} combat signals by Steam Age...")

        # Define buckets
        buckets = [0, 5000, 8000, 15000, 30000, 60000, 1000000]
        labels = ['0-5s', '5-8s', '8-15s', '15-30s', '30-60s', '>60s']
        merged['age_bucket'] = pd.cut(merged['steam_age_ms'], bins=buckets, labels=labels)

        summary = merged.groupby('age_bucket')['markout_30s'].agg(['count', 'mean', 'median'])
        
        # Win rate
        def win_rate(x): return (x > 0).mean()
        wr = merged.groupby('age_bucket')['markout_30s'].apply(win_rate)
        summary['win_rate'] = wr
        
        print("\n=== Markout@30s by Steam Age Bucket ===")
        print(summary)

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    analyze()
