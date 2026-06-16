import pandas as pd
import numpy as np

def analyze():
    try:
        # 1. Load data
        df_mark = pd.read_csv('logs/signal_markouts.csv')
        df_mark = df_mark.rename(columns={'signal_timestamp_utc': 'timestamp_utc'})
        # Drop duplicates to avoid join errors
        df_mark = df_mark.drop_duplicates(subset=['timestamp_utc', 'match_id'])
        
        value_sigs = df_mark[df_mark['event_type'] == 'POLL_VALUE_DISAGREEMENT'].copy()
        
        if value_sigs.empty:
            print("No VALUE_DISAGREEMENT signals found.")
            return

        print(f"=== DEEP AUDIT: POLL_VALUE_DISAGREEMENT (n={len(value_sigs)}) ===")

        # 2. Performance over time
        horizons = ['markout_3s', 'markout_10s', 'markout_30s']
        print("\nAlpha Decay Curve (Mean):")
        print(value_sigs[horizons].mean())
        
        # 3. Join with signals.csv to get internal model metrics
        df_sig = pd.read_csv('logs/signals.csv')
        df_sig = df_sig[df_sig['event_type'] == 'POLL_VALUE_DISAGREEMENT']
        df_sig = df_sig.drop_duplicates(subset=['timestamp_utc', 'match_id'])
        
        merged = pd.merge(df_sig, value_sigs[['timestamp_utc', 'match_id', 'markout_30s']], 
                         on=['timestamp_utc', 'match_id'], how='inner')

        if not merged.empty:
            merged['slow_model_fair'] = pd.to_numeric(merged['slow_model_fair'], errors='coerce')
            merged['executable_edge'] = pd.to_numeric(merged['executable_edge'], errors='coerce')
            merged['markout_30s'] = pd.to_numeric(merged['markout_30s'], errors='coerce')
            
            print("\nModel Correlation with M30:")
            print(merged[['slow_model_fair', 'executable_edge', 'markout_30s']].corr()['markout_30s'])
            
            # Performance by Executable Edge
            print("\nPerformance by Edge Magnitude:")
            merged['edge_mag'] = merged['executable_edge'].abs()
            merged['edge_bucket'] = pd.qcut(merged['edge_mag'], 3, labels=['low', 'mid', 'high'], duplicates='drop')
            print(merged.groupby('edge_bucket', observed=False)['markout_30s'].mean())

        # 4. Top Winners
        winners = value_sigs.sort_values('markout_30s', ascending=False).head(10)
        print("\nTop Winning Value Disagreements:")
        print(winners[['match_id', 'reference_ask', 'markout_30s']].to_string(index=False))

    except Exception as e:
        import traceback
        traceback.print_exc()

analyze()
