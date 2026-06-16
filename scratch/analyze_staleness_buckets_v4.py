import pandas as pd
import numpy as np

def analyze():
    try:
        df_lat = pd.read_csv('logs/latency.csv')
        df_mark = pd.read_csv('logs/signal_markouts.csv')
        
        # combat only
        combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
        df_mark = df_mark[df_mark['event_type'].isin(combat_types)]
        
        # Merge on match_id and game_time_sec
        # We need to ensure types match (str vs int)
        df_lat['match_id'] = df_lat['match_id'].astype(str)
        df_mark['match_id'] = df_mark['match_id'].astype(str)
        
        # In signal_markouts, there is no game_time_sec. 
        # But signals.csv has it. Let's use shadow_trades.csv which HAS game_time_sec and markouts.
        shadow = pd.read_csv('logs/shadow_trades.csv')
        shadow = shadow[shadow['event_type'].isin(combat_types)]
        shadow['match_id'] = shadow['match_id'].astype(str)
        
        merged = pd.merge(df_lat[['match_id', 'game_time_sec', 'steam_source_update_age_sec']], 
                         shadow[['match_id', 'game_time_sec', 'markout_30s', 'event_type']], 
                         on=['match_id', 'game_time_sec'], how='inner')
        
        if merged.empty:
            print("Join failed.")
            return

        print(f"Analyzing {len(merged)} combat signals by Source Age...")

        # steam_source_update_age_sec is in seconds
        buckets = [0, 3, 5, 8, 15, 30, 1000]
        labels = ['0-3s', '3-5s', '5-8s', '8-15s', '15-30s', '>30s']
        merged['age_bucket'] = pd.cut(merged['steam_source_update_age_sec'], bins=buckets, labels=labels)

        summary = merged.groupby('age_bucket', observed=False)['markout_30s'].agg(['count', 'mean', 'median'])
        def win_rate(x): return (x > 0).mean()
        summary['win_rate'] = merged.groupby('age_bucket', observed=False)['markout_30s'].apply(win_rate)
        
        print("\n=== Markout@30s by Source Update Age Bucket ===")
        print(summary)

    except Exception as e:
        print(f"Error: {e}")

analyze()
