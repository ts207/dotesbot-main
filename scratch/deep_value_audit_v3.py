import pandas as pd
import numpy as np

def analyze():
    try:
        # Load signals and markouts
        df_sig = pd.read_csv('logs/signals.csv')
        df_mark = pd.read_csv('logs/signal_markouts.csv')
        df_mark = df_mark.rename(columns={'signal_timestamp_utc': 'timestamp_utc'})
        
        # Filter for Value Disagreement
        sigs = df_sig[df_sig['event_type'] == 'POLL_VALUE_DISAGREEMENT'].copy()
        mark = df_mark[df_mark['event_type'] == 'POLL_VALUE_DISAGREEMENT'].copy()
        
        print(f"=== DEEP AUDIT: POLL_VALUE_DISAGREEMENT (Signals: {len(sigs)}, Markouts: {len(mark)}) ===")

        # 1. Direct Alpha Analysis (No join needed)
        print("\n--- Tactical Alpha Profile ---")
        horizons = ['markout_3s', 'markout_10s', 'markout_30s']
        print(mark[horizons].mean())
        print(f"Win Rate (30s): {(mark['markout_30s'] > 0).mean():.1%}")

        # 2. Match-Level Performance
        print("\n--- Top Performers by Match ---")
        print(mark.groupby('match_id')['markout_30s'].mean().sort_values(ascending=False).head(5))

        # 3. Model Accuracy Check
        # Join to see if high-edge signals performed better
        # Use match_id and round timestamp to 100ms
        sigs['ts_round'] = pd.to_datetime(sigs['timestamp_utc']).dt.round('100L')
        mark['ts_round'] = pd.to_datetime(mark['timestamp_utc']).dt.round('100L')
        
        merged = pd.merge(sigs[['ts_round', 'match_id', 'executable_edge', 'slow_model_fair']], 
                         mark[['ts_round', 'match_id', 'markout_30s']], 
                         on=['ts_round', 'match_id'], how='inner')
        
        if not merged.empty:
            merged['markout_30s'] = pd.to_numeric(merged['markout_30s'], errors='coerce')
            merged['executable_edge'] = pd.to_numeric(merged['executable_edge'], errors='coerce')
            
            print("\n--- Edge vs. Realized Alpha ---")
            merged['edge_bucket'] = pd.qcut(merged['executable_edge'], 3, labels=['low', 'mid', 'high'])
            print(merged.groupby('edge_bucket', observed=False)['markout_30s'].agg(['count', 'mean']))

    except Exception as e:
        print(f"Error: {e}")

analyze()
