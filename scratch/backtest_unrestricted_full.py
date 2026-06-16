import pandas as pd
import numpy as np

def run_backtest():
    try:
        # Load raw markouts
        df = pd.read_csv('logs/signal_markouts.csv')
        # Combat only
        combat = df[df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])].copy()
        
        print(f"Total Raw Combat Fires: {len(combat)}")

        # 1. Logic: Current portfolio (15s/90c/Combat) but WITHOUT repricing/momentum checks
        # These are signals that were skipped for:
        # - chasing_terminal_price
        # - already_repriced
        # - momentum_exhausted
        
        # We find them in signal_markouts.csv where decision was 'skip' 
        # and skip_reason is one of the above.
        
        # Filter: only signals <= 0.90 fill (as per our new cap)
        combat = combat[combat['reference_ask'] <= 0.90]
        
        print(f"\n--- Strategy: 90c Cap / NO Repricing Filter (n={len(combat)}) ---")
        
        mean_m30 = combat['markout_30s'].mean()
        win_r30 = (combat['markout_30s'] > 0).mean()
        
        print(f"Win Rate (30s): {win_r30:.1%}")
        print(f"Mean Alpha (30s): {mean_m30:+.4f}c")
        
        proj_pnl = combat['markout_30s'].sum() * 100 # $100 stake
        print(f"Projected Tactical Profit ($100 stake): ${proj_pnl:+.2f}")

    except Exception as e:
        print(f"Error: {e}")

run_backtest()
