import pandas as pd
import numpy as np

def run_replay():
    try:
        # Load combat only
        df = pd.read_csv('logs/signal_markouts.csv')
        combat = df[df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])].copy()
        
        # Strategy: 
        # Exit at 3s if it's the peak, otherwise exit at 30s.
        # This is the "Capture Local Peak #1 or #2" logic.
        
        results = []
        for _, row in combat.iterrows():
            m3, m10, m30 = row['markout_3s'], row['markout_10s'], row['markout_30s']
            
            # Logic: If 3s is a "pop" (>2c) and then it starts to retrace by 10s, take the 3s price.
            if m3 >= 0.02 and m10 < m3:
                pnl = m3
            else:
                # Otherwise, ride the drift to the 30s technical peak.
                pnl = m30
            
            results.append(pnl)
            
        print(f"=== Historical Replay Results (n={len(combat)}) ===")
        print(f"Avg Realized PnL: {np.mean(results):+.4f}")
        print(f"Total ($100 stake): ${np.sum(results)*100:.2f}")
        print(f"Gain vs Fixed 30s: {(np.mean(results) - combat['markout_30s'].mean()):+.4f}")

    except Exception as e:
        print(f"Error: {e}")

run_replay()
