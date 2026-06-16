import pandas as pd
import numpy as np

def analyze():
    df = pd.read_csv('logs/signal_markouts.csv')
    combat = df[df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])]
    
    # Calculate the PnL of capturing the HIGHEST of the three points
    combat['peak'] = combat[['markout_3s', 'markout_10s', 'markout_30s']].max(axis=1)
    
    print(f"=== Perfect Peak Capture (Theoretical Limit) ===")
    print(f"Avg Peak PnL: {combat['peak'].mean():+.4f}")
    print(f"Total ($100 stake): ${combat['peak'].sum() * 100:.2f}")
    
    # Check if a 1c trailing stop captures this
    # (requires knowing the tick-by-tick move between 3s and 30s)
    
analyze()
