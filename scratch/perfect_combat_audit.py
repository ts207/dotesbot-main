import pandas as pd
import numpy as np

def run_audit():
    df = pd.read_csv('logs/signal_markouts.csv')
    combat = df[df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])].copy()
    combat = combat.dropna(subset=['markout_3s', 'markout_10s', 'markout_30s'])
    
    # PEAK CAPTURE: 
    # If we catch the peak at 3s OR 10s OR 30s
    combat['peak'] = combat[['markout_3s', 'markout_10s', 'markout_30s']].max(axis=1)
    
    print(f"Audit of {len(combat)} historical combat signals:")
    print(f"Fixed 30s Total:  ${combat['markout_30s'].sum() * 100:.2f}")
    print(f"Perfect Peak Total: ${combat['peak'].sum() * 100:.2f}")
    print(f"Theoretical Gain:   ${(combat['peak'].sum() - combat['markout_30s'].sum()) * 100:.2f}")

run_audit()
