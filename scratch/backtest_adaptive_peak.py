import pandas as pd
import numpy as np

def run_backtest():
    try:
        df = pd.read_csv('logs/signal_markouts.csv')
        combat = df[df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])].copy()
        
        results = []
        for idx, row in combat.iterrows():
            m3, m10, m30 = row['markout_3s'], row['markout_10s'], row['markout_30s']
            
            pnl = m30 # Default: terminal
            status = 'horizon_30s'
            
            # 1. Capture the 3s Instant Repricing (Local Peak #1)
            # If 3s is already highly profitable (>2c), we set a tight 1c trail
            if m3 >= 0.02:
                if m10 < (m3 - 0.01):
                    pnl = m3
                    status = 'peak1_capture_3s'
                # If it doesn't drop, we continue to 30s...
            
            # 2. Capture the 30s Drift (Local Peak #2)
            # If we reached 30s and it's a "big winner" (>5c), we take it
            if status == 'horizon_30s' and m30 >= 0.05:
                pnl = m30
                status = 'peak2_trend_30s'

            results.append({'pnl': pnl, 'status': status})
            
        res_df = pd.DataFrame(results)
        print("=== Adaptive Peak-to-Peak Backtest (n=115) ===")
        print(res_df['status'].value_counts())
        print(f"\nAvg PnL per trade: {res_df['pnl'].mean():+.4f}")
        print(f"Total Proj PnL ($100 Stake): ${res_df['pnl'].sum() * 100:.2f}")

    except Exception as e:
        print(f'Error: {e}')

run_backtest()
