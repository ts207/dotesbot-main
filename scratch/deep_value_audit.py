import pandas as pd
import numpy as np

def analyze():
    try:
        # 1. Load data
        df = pd.read_csv('logs/signal_markouts.csv')
        value_sigs = df[df['event_type'] == 'POLL_VALUE_DISAGREEMENT'].copy()
        
        if value_sigs.empty:
            print("No VALUE_DISAGREEMENT signals found.")
            return

        print(f"=== DEEP AUDIT: POLL_VALUE_DISAGREEMENT (n={len(value_sigs)}) ===")

        # 2. Performance over time
        horizons = ['markout_3s', 'markout_10s', 'markout_30s']
        print("\nAlpha Decay Curve (Mean):")
        print(value_sigs[horizons].mean())
        
        # 3. Join with signals.csv to get internal model metrics
        # We need slow_model_fair and fair_price
        df_sig = pd.read_csv('logs/signals.csv')
        df_sig = df_sig[df_sig['event_type'] == 'POLL_VALUE_DISAGREEMENT']
        
        merged = pd.merge(df_sig, df_sig[['timestamp_utc', 'match_id', 'slow_model_fair', 'fair_price', 'executable_edge']], 
                         on=['timestamp_utc', 'match_id'], how='inner')
        
        # Note: join on timestamp_utc in markouts needs mapping to signal_timestamp_utc
        df_mark = pd.read_csv('logs/signal_markouts.csv')
        df_mark = df_mark.rename(columns={'signal_timestamp_utc': 'timestamp_utc'})
        merged = pd.merge(df_sig, df_mark[['timestamp_utc', 'match_id', 'markout_30s']], 
                         on=['timestamp_utc', 'match_id'], how='inner')

        if not merged.empty:
            print("\nModel Metrics correlation with M30:")
            merged['slow_model_fair'] = pd.to_numeric(merged['slow_model_fair'], errors='coerce')
            merged['markout_30s'] = pd.to_numeric(merged['markout_30s'], errors='coerce')
            print(merged[['slow_model_fair', 'executable_edge', 'markout_30s']].corr()['markout_30s'])
            
            # Bucket by Edge
            merged['edge_bucket'] = pd.qcut(merged['executable_edge'], 3, labels=['low', 'mid', 'high'])
            print("\nPerformance by Edge Bucket:")
            print(merged.groupby('edge_bucket', observed=False)['markout_30s'].mean())

        # 4. Success Story: Biggest Winners
        winners = value_sigs.sort_values('markout_30s', ascending=False).head(5)
        print("\nTop Winning Value Disagreements:")
        print(winners[['match_id', 'reference_ask', 'markout_30s']].to_string(index=False))

    except Exception as e:
        print(f"Error: {e}")

analyze()
