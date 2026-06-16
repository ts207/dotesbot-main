import pandas as pd
import glob
import numpy as np

def analyze():
    try:
        df = pd.read_csv('logs/signal_markouts.csv')
        combat = df[df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])].copy()
        
        # Calculate local peak for every trade
        combat['peak_val'] = combat[['markout_3s', 'markout_10s', 'markout_30s']].max(axis=1)
        
        def find_peak_time(row):
            if row['peak_val'] == row['markout_3s']: return 3
            if row['peak_val'] == row['markout_10s']: return 10
            return 30
            
        combat['peak_time'] = combat.apply(find_peak_time, axis=1)
        
        print(f"Auditing {len(combat)} trades individually:")
        print(combat.groupby('peak_time')['peak_val'].agg(['count', 'mean', 'median']))

        # Check for spread impact in the raw log
        df_sig = pd.read_csv('logs/signals.csv')
        df_sig['spread_c'] = pd.to_numeric(df_sig['spread'], errors='coerce')
        print("\nAvg Entry Spread (cents):", df_sig['spread_c'].mean())

    except Exception as e:
        print(e)

analyze()
