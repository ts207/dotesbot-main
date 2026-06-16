import pandas as pd
import numpy as np

def run_backtest():
    try:
        df = pd.read_csv('logs/signal_markouts.csv')
        combat = df[df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])].copy()
        
        results = []
        for idx, row in combat.iterrows():
            m3, m10, m30 = row['markout_3s'], row['markout_10s'], row['markout_30s']
            
            pnl = m30 # Default: final realized
            status = 'horizon_30s'
            peak = 0
            
            # Sequence simulation: 3s -> 10s -> 30s
            for time_step, markout in [('3s', m3), ('10s', m10), ('30s', m30)]:
                # 1. Update running peak
                if markout > peak:
                    peak = markout
                
                # 2. Stage 1: Lock in the "Pop" (First 10s)
                # If we've seen a peak > 1.5c and it drops 1c, take the money.
                if time_step in ['3s', '10s'] and peak >= 0.015 and markout <= peak - 0.01:
                    pnl = markout
                    status = f'stage1_peak_lock_{time_step}'
                    break
                
                # 3. Stage 2: Long Drift (30s)
                # If we are at 30s and price is strong (>3c), take it.
                if time_step == '30s' and markout >= 0.03:
                    pnl = 0.03
                    status = 'stage2_tp_30s'
                    break
                
                # 4. Emergency Stop
                if markout <= -0.05:
                    pnl = markout
                    status = f'stopped_at_{time_step}'
                    break
            
            results.append({'pnl': pnl, 'status': status})
            
        res_df = pd.DataFrame(results)
        print("=== Two-Stage 'Peak-to-Peak' Backtest (n=115) ===")
        print(res_df['status'].value_counts())
        print(f"\nAvg PnL per trade: {res_df['pnl'].mean():+.4f}")
        print(f"Total Proj PnL ($100 Stake): ${res_df['pnl'].sum() * 100:.2f}")

    except Exception as e:
        print(f'Error: {e}')

run_backtest()
