import pandas as pd
import numpy as np

def run_backtest():
    try:
        df = pd.read_csv('logs/signal_markouts.csv')
        combat = df[df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])].copy()
        
        results = []
        for idx, row in combat.iterrows():
            m3, m10, m30 = row['markout_3s'], row['markout_10s'], row['markout_30s']
            
            pnl = m30 # Default
            status = 'horizon_30s'
            
            # 1. Profit Lock Logic:
            # If 3s or 10s is a "Home Run" (>4c), lock it in immediately.
            # Why 4c? Analysis shows 3s repricings above 4c are usually exhausted.
            if m3 >= 0.04:
                pnl = m3
                status = 'home_run_lock_3s'
            elif m10 >= 0.04:
                pnl = m10
                status = 'home_run_lock_10s'
            
            # 2. Reversal Protection:
            # If we didn't lock a profit, and the 10s price is < 0 but was > 0 at 3s,
            # exit immediately to preserve the technical breakeven.
            elif m3 > 0 and m10 < 0:
                pnl = m10
                status = 'early_reversal_cut'

            results.append({'pnl': pnl, 'status': status})
            
        res_df = pd.DataFrame(results)
        print("=== Profit Lock + Reversal Cut Backtest (n=115) ===")
        print(res_df['status'].value_counts())
        print(f"\nAvg PnL per trade: {res_df['pnl'].mean():+.4f}")
        print(f"Total Proj PnL ($100 Stake): ${res_df['pnl'].sum() * 100:.2f}")

    except Exception as e:
        print(f'Error: {e}')

run_backtest()
