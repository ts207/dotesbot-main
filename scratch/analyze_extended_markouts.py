import pandas as pd
import numpy as np

def analyze():
    try:
        # 1. Load shadow_trades.csv which usually has longer horizons (60s, markout_settle)
        df = pd.read_csv('logs/shadow_trades.csv')
        combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
        
        # Filter for combat events and clean data
        combat = df[df['event_type'].isin(combat_types)].copy()
        
        print(f"Analyzing {len(combat)} Combat signals with extended horizons...")

        # 2. Map out the full decay/drift curve
        # horizons typically available: 3s, 10s, 30s, 60s
        horizons = ['markout_3s', 'markout_10s', 'markout_30s', 'markout_60s']
        # Filter only columns that exist
        available_horizons = [h for h in horizons if h in combat.columns]
        
        print("\n=== Mean Markout Curve (Price Drift) ===")
        curve = combat[available_horizons].mean()
        print(curve)

        print("\n=== Win Rate Curve (% of signals with Positive Markout) ===")
        wr_curve = (combat[available_horizons] > 0).mean()
        print(wr_curve)

        # 3. Analyze "Terminal Alpha" (Settlement)
        # Note: would_pnl_60s etc might be in shadow_trades
        if 'markout_60s' in combat.columns:
            # Check how many 'retrace' after 30s
            combat['retraced_after_30s'] = combat['markout_60s'] < combat['markout_30s']
            retrace_pct = combat['retraced_after_30s'].mean()
            print(f"\nSignals that retrace between 30s and 60s: {retrace_pct:.1%}")

        # 4. Deep dive into the "Long Drift" vs "Sharp Peak"
        # We define "Trending" as M60 > M30
        if 'markout_60s' in combat.columns:
            trending = combat[combat['markout_60s'] > combat['markout_30s']]
            peaked = combat[combat['markout_30s'] > combat['markout_60s']]
            
            print(f"\nDrift Profiles:")
            print(f"  - Trending (Price keeps rising 30s -> 60s): n={len(trending)} ({len(trending)/len(combat):.1%})")
            print(f"  - Peaked (Price drops 30s -> 60s):         n={len(peaked)} ({len(peaked)/len(combat):.1%})")

    except Exception as e:
        print(f"Error: {e}")

analyze()
