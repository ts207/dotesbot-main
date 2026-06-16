import pandas as pd
import numpy as np

def run_audit():
    df = pd.read_csv('logs/signal_markouts.csv')
    combat = df[df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])]
    
    # PEAK CAPTURE: 
    # If we catch the peak at 3s OR 10s OR 30s
    combat['peak'] = combat[['markout_3s', 'markout_10s', 'markout_30s']].max(axis=1)
    
    print(f"Realistic Total Profit (Capturing Local Peaks): ${combat['peak'].sum() * 100:.2f}")

run_audit()
