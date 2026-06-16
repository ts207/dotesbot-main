import pandas as pd
import numpy as np

def analyze():
    try:
        df = pd.read_csv('logs/signal_markouts.csv')
        # Filter for combat only + optimized logic (90c cap)
        combat = df[
            (df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])) &
            (df['reference_ask'] <= 0.90)
        ].copy()
        
        print(f"Analyzing {len(combat)} high-conviction combat signals...")

        # Find where the peak happens for EACH trade
        def find_peak(row):
            m3, m10, m30 = row['markout_3s'], row['markout_10s'], row['markout_30s']
            best = max(m3, m10, m30)
            if best == m3: return 3
            if best == m10: return 10
            return 30

        combat['peak_time'] = combat.apply(find_peak, axis=1)
        
        print("\n=== When does the alpha peak for each trade? ===")
        peak_counts = combat['peak_time'].value_counts().sort_index()
        for time, count in peak_counts.items():
            print(f"Peak at {time:>2}s: {count:>2} trades ({count/len(combat):.1%})")

        print("\n=== Mean Alpha at each step ===")
        print(combat[['markout_3s', 'markout_10s', 'markout_30s']].mean())

    except Exception as e:
        print(f"Error: {e}")

analyze()
