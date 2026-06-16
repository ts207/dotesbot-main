import pandas as pd
import numpy as np

def run_backtest():
    # Load historical markouts
    try:
        df = pd.read_csv('logs/signal_markouts.csv')
        combat = df[df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])].copy()
        
        # We only have 3s, 10s, 30s snapshots. 
        # Strategy: 
        # - Target: Fair + 0.03
        # - Stop: Fair - (current technical spread toll, e.g. 0.01)
        
        results = []
        for idx, row in combat.iterrows():
            # Check horizons sequentially to simulate time
            m3, m10, m30 = row['markout_3s'], row['markout_10s'], row['markout_30s']
            
            pnl = -0.01 # Assume initial exit if nothing happens (spread loss)
            status = 'horizon_exit'
            
            # Sequence simulation: 3s -> 10s -> 30s
            for time_step, markout in [('3s', m3), ('10s', m10), ('30s', m30)]:
                if markout >= 0.03:
                    pnl = 0.03
                    status = f'tp_at_{time_step}'
                    break
                if markout <= -0.01:
                    pnl = markout
                    status = f'stopped_at_{time_step}'
                    break
            
            results.append({'pnl': pnl, 'status': status})
            
        res_df = pd.DataFrame(results)
        print(f"=== Take-Profit (+3c) vs Stop (-1c) Backtest (n={len(combat)}) ===")
        print(res_df['status'].value_counts())
        print(f"\nAvg PnL per trade: {res_df['pnl'].mean():+.4f}")
        print(f"Win Rate (TP hit): {(res_df['pnl'] > 0).mean():.1%}")
        print(f"Total Proj PnL ($100 Stake): ${res_df['pnl'].sum() * 100:.2f}")

    except Exception as e:
        print(f"Error: {e}")

run_backtest()
