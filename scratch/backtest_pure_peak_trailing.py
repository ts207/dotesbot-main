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
            
            # Simulated Trailing Stop: 1c Trail armed at 1c profit
            peak = 0
            armed = False
            
            for time_step, markout in [('3s', m3), ('10s', m10), ('30s', m30)]:
                if markout > peak:
                    peak = markout
                if peak >= 0.01:
                    armed = True
                
                # If armed and price drops 1c from peak, EXIT
                if armed and markout <= (peak - 0.01):
                    pnl = markout
                    status = f'trailing_exit_{time_step}'
                    break
                    
            results.append({'pnl': pnl, 'status': status})
            
        res_df = pd.DataFrame(results)
        print("=== 1c Trailing Stop Backtest (n=115) ===")
        print(res_df['status'].value_counts())
        print(f"\nAvg PnL per trade: {res_df['pnl'].mean():+.4f}")
        print(f"Total Proj PnL ($100 Stake): ${res_df['pnl'].sum() * 100:.2f}")

    except Exception as e:
        print(f'Error: {e}')

run_backtest()
