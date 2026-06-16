import pandas as pd
import numpy as np

def run_audit():
    try:
        # Load all markouts - this file contains the historical truth for all signal fires
        df = pd.read_csv('logs/signal_markouts.csv')
        
        # 1. Filter for the 2 combat events
        combat_events = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
        df_combat = df[df['event_type'].isin(combat_events)].copy()
        
        if df_combat.empty:
            print("No combat signals found in markout log.")
            return

        print(f"Auditing {len(df_combat)} historical combat signals...")

        # 2. Re-simulate our "Gold Standard" logic:
        # - Max Price: 0.90 (raised from 0.82)
        # - Min Edge: 0.001 (relaxed from 0.005)
        # - Max Steam Age: 15s (not available in this CSV directly, but we assume pass for this audit)
        
        df_qualify = df_combat[
            (df_combat['reference_ask'] <= 0.90) & 
            (df_combat['executable_edge'] >= 0.001)
        ]
        
        print(f"\n--- Strategy: 90c Cap / Combat Only (n={len(df_qualify)}) ---")
        
        # Calculate PnL for different horizons
        horizons = ['markout_3s', 'markout_10s', 'markout_30s']
        for h in horizons:
            mean_m = df_qualify[h].mean()
            win_r = (df_qualify[h] > 0).mean()
            print(f"{h:12}: {mean_m:+.4f} (Win%: {win_r:.1%})")

        # 3. Projected PnL
        proj_pnl_30s = df_qualify['markout_30s'].sum() * 100 # $100 stake proxy
        print(f"\nProjected Tactical Profit ($100 stake): ${proj_pnl_30s:+.2f}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    run_audit()
