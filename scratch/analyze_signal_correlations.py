import pandas as pd
import numpy as np

def analyze():
    try:
        # Load all markouts
        df = pd.read_csv('logs/signal_markouts.csv')
        combat = df[df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])].copy()
        
        # Clean
        combat = combat.dropna(subset=['markout_3s', 'markout_10s', 'markout_30s'])
        
        print(f"Analyzing {len(combat)} historical combat signal profiles...")

        # 1. Profile 1: The "Pop and Fade" (Sharp reaction, immediate decay)
        pop_and_fade = combat[(combat['markout_3s'] > combat['markout_30s']) & (combat['markout_3s'] > 0)]
        
        # 2. Profile 2: The "Slow Burn" (No early move, large late move)
        slow_burn = combat[(combat['markout_30s'] > combat['markout_3s']) & (combat['markout_30s'] > 0)]
        
        # 3. Profile 3: The "Fake Out" (Pop early, then crashes below entry)
        fake_out = combat[(combat['markout_3s'] > 0) & (combat['markout_30s'] < 0)]

        print("\n=== Event-Driven Price Profiles ===")
        print(f"Pop and Fade: {len(pop_and_fade)} trades ({len(pop_and_fade)/len(combat):.1%})")
        print(f"  - Price jumps at 3s, then slowly decays back toward entry.")
        print(f"Slow Burn:    {len(slow_burn)} trades ({len(slow_burn)/len(combat):.1%})")
        print(f"  - Little reaction at 3s, but price trends strongly by 30s.")
        print(f"Fake Out:     {len(fake_out)} trades ({len(fake_out)/len(combat):.1%})")
        print(f"  - Trap. Price looks good at 3s but turns into a loss by 30s.")

        # 4. Correlation: Does a 3s pop predict a 30s win?
        corr = combat['markout_3s'].corr(combat['markout_30s'])
        print(f"\nCorrelation (3s vs 30s): {corr:.4f}")
        
    except Exception as e:
        print(f"Error: {e}")

analyze()
