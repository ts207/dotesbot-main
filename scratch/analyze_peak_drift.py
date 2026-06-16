import pandas as pd
import numpy as np

def analyze_peak():
    try:
        df = pd.read_csv('logs/signal_markouts.csv')
        combat = df[df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])].copy()
        
        results = []
        for idx, row in combat.iterrows():
            m3, m10, m30 = row['markout_3s'], row['markout_10s'], row['markout_30s']
            
            # Find the local peak (best markout) among the three snapshots
            peak_val = max(m3, m10, m30)
            
            # Identify when the peak occurred
            if peak_val == m3: peak_step = 3
            elif peak_val == m10: peak_step = 10
            else: peak_step = 30
            
            # Identify the "Decay" (loss from peak to 30s)
            decay = peak_val - m30 if peak_step < 30 else 0
            
            results.append({
                'match_id': row['match_id'],
                'peak_val': peak_val,
                'peak_step': peak_step,
                'm30': m30,
                'decay': decay
            })
            
        res_df = pd.DataFrame(results)
        print("=== Combat Peak Repricing Audit (n=115) ===")
        print(f"Avg Theoretical Peak: {res_df['peak_val'].mean():+.4f}")
        print(f"Avg Realized @ 30s:    {res_df['m30'].mean():+.4f}")
        print(f"Avg 'Decay' (Missed):  {res_df['decay'].mean():.4f}")
        
        print("\nWhen does the peak happen?")
        print(res_df['peak_step'].value_counts(normalize=True).sort_index())

    except Exception as e:
        print(f'Error: {e}')

analyze_peak()
