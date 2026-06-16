import pandas as pd
import numpy as np

def run_backtest():
    try:
        # Load all markouts - represents every signal fire regardless of skips
        df = pd.read_csv('logs/signal_markouts.csv')
        
        # 1. Filter for the 2 combat events
        combat_events = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
        df_combat = df[df['event_type'].isin(combat_events)].copy()
        
        if df_combat.empty:
            print("No combat signals found.")
            return

        print(f"Auditing {len(df_combat)} Total Combat Signal Fires...")

        # 2. Configuration:
        # - 90c Cap (ask <= 0.90)
        # - NO Repricing Filter (ignoring chasing_terminal_price and already_repriced)
        # - 15s Steam Gate (assumed pass for this audit)
        # - Min Edge: 0.001
        
        df_unrestricted = df_combat[
            (df_combat['reference_ask'] <= 0.90) &
            (df_combat['executable_edge'] >= 0.001)
        ]
        
        print(f"\n--- Strategy: 90c Cap / NO Repricing Filter (n={len(df_unrestricted)}) ---")
        
        # Calculate PnL
        mean_m30 = df_unrestricted['markout_30s'].mean()
        win_r30 = (df_unrestricted['markout_30s'] > 0).mean()
        
        print(f"Win Rate (30s): {win_r30:.1%}")
        print(f"Mean Alpha (30s): {mean_m30:+.4f}c")
        
        proj_pnl = df_unrestricted['markout_30s'].sum() * 100 # $100 stake
        print(f"Projected Tactical Profit ($100 stake): ${proj_pnl:+.2f}")

        # 3. Compare with "Repricing Filter ON" (Existing Chasing Terminal check)
        # We simulate the filter by removing things that peaked at 3s and decayed by 30s
        # or were already very expensive.
        df_restricted = df_unrestricted[df_unrestricted['markout_3s'] <= 0.04] # Proxy for 'already_repriced'
        
        print(f"\n--- Strategy: 90c Cap / WITH Repricing Filter (n={len(df_restricted)}) ---")
        print(f"Win Rate (30s): {(df_restricted['markout_30s'] > 0).mean():.1%}")
        print(f"Mean Alpha (30s): {df_restricted['markout_30s'].mean():+.4f}c")
        print(f"Projected Tactical Profit ($100 stake): ${df_restricted['markout_30s'].sum() * 100:+.2f}")

    except Exception as e:
        print(f"Error: {e}")

run_backtest()
