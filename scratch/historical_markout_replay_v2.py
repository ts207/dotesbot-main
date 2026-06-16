import pandas as pd
import numpy as np

def run_replay():
    try:
        df = pd.read_csv('logs/signal_markouts.csv')
        combat = df[df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])].copy()
        
        # Clean data: drop rows with any NaNs in our markout columns
        combat = combat.dropna(subset=['markout_3s', 'markout_10s', 'markout_30s'])
        
        results = []
        for _, row in combat.iterrows():
            m3, m10, m30 = row['markout_3s'], row['markout_10s'], row['markout_30s']
            
            # Logic: If 3s is a "pop" (>1c) and then it starts to retrace by 10s, take the 3s price.
            if m3 >= 0.01 and m10 < m3:
                pnl = m3
            else:
                pnl = m30
            
            results.append(pnl)
            
        print(f"=== Historical Replay Results (n={len(combat)}) ===")
        print(f"Avg Realized PnL: {np.mean(results):+.4f}")
        print(f"Total ($100 stake): ${np.sum(results)*100:.2f}")
        print(f"Gain vs Fixed 30s: {(np.mean(results) - combat['markout_30s'].mean()):+.4f}")

    except Exception as e:
        print(f"Error: {e}")

run_replay()
