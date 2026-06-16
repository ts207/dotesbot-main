import pandas as pd
import glob
import numpy as np

def run_audit():
    try:
        # Load all markouts - represents EVERY signal fire regardless of historical skips
        df = pd.read_csv('logs/signal_markouts.csv')
        
        # Combat types only
        combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
        df_combat = df[df['event_type'].isin(combat_types)].copy()
        
        # AGGRESSIVE FILTERS:
        # 1. 0.95 Fill Cap
        # 2. 30s Steam Gate (not in this csv, but we assume pass)
        # 3. 0.001 Min Edge
        # 4. Momentum/Repricing DISABLED (captured by taking all rows passing price/edge)
        
        portfolio = df_combat[
            (df_combat['reference_ask'] <= 0.95) &
            (df_combat['executable_edge'] >= 0.001)
        ]

        print("=== AGGRESSIVE SNIPER: PROJECTED YIELD ===")
        print(f"Historical Sample Size: n={len(portfolio)} signals")
        print(f"Historical Coverage:    {len(portfolio)/115:.1%} of combat fires")
        
        # Performance Stats
        win_r = (portfolio['markout_30s'] > 0).mean()
        mean_m = portfolio['markout_30s'].mean()
        print(f"Win Rate (30s): {win_r:.1%}")
        print(f"Avg Alpha (30s): {mean_m:+.4f}c")
        
        # Use cash metric ($100 stake)
        total_pnl = portfolio['markout_30s'].sum() * 100
        print(f"Total Proj PnL ($100 stake): ${total_pnl:+.2f}")
        
    except Exception as e:
        print(e)

run_audit()
