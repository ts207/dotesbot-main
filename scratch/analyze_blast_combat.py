import pandas as pd
import numpy as np

def analyze():
    try:
        df = pd.read_csv('logs/signal_markouts.csv')
        # Filter for BLAST Slam AND the two gold combat events
        combat_events = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
        blast_combat = df[
            (df['market_name'].str.contains('BLAST Slam', na=False, case=False)) & 
            (df['event_type'].isin(combat_events))
        ].copy()
        
        if blast_combat.empty:
            print("No BLAST Slam combat signals (FIGHT_SWING/LATE_FLIP) found in signal_markouts.csv.")
            # Let's check why - maybe they are in the older rotated logs?
            return

        print(f"Analyzing {len(blast_combat)} BLAST Slam Combat Signals...")

        # 1. Tactical Repricing Curve
        horizons = ['markout_3s', 'markout_10s', 'markout_30s']
        print("\n--- Tactical Repricing Curve (Mean Markout) ---")
        for h in horizons:
            mean_m = blast_combat[h].mean()
            win_r = (blast_combat[h] > 0).mean()
            print(f"{h:12}: {mean_m:+.4f} (Win%: {win_r:.1%})")

        # 2. Detail by Event Type
        print("\n--- Detail by Event Type (30s) ---")
        detail = blast_combat.groupby('event_type')['markout_30s'].agg(['count', 'mean', 'median', 'std'])
        print(detail)

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    analyze()
