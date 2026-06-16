import pandas as pd
import numpy as np

def run_backtest():
    try:
        df = pd.read_csv('logs/signal_markouts.csv')
        combat = df[df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])].copy()
        
        results = []
        for idx, row in combat.iterrows():
            m3, m10, m30 = row['markout_3s'], row['markout_10s'], row['markout_30s']
            
            pnl = m30 # Default to 30s exit if no TP/SL hit
            status = 'horizon_30s'
            
            # Sequence simulation: 3s -> 10s -> 30s
            for time_step, markout in [('3s', m3), ('10s', m10), ('30s', m30)]:
                # Take Profit at +3c
                if markout >= 0.03:
                    pnl = 0.03
                    status = f'tp_at_{time_step}'
                    break
                # Stop Loss at -5c (Technical Dip)
                if markout <= -0.05:
                    pnl = markout
                    status = f'stopped_at_{time_step}'
                    break
            
            results.append({'pnl': pnl, 'status': status})
            
        res_df = pd.DataFrame(results)
        print(f"=== Take-Profit (+3c) vs Stop (-5c) Backtest (n={len(combat)}) ===")
        print(res_df['status'].value_counts())
        print(f"\nAvg PnL per trade: {res_df['pnl'].mean():+.4f}")
        print(f"Win Rate (TP hit): {(res_df['pnl'] > 0).mean():.1%}")
        print(f"Total Proj PnL ($100 Stake): ${res_df['pnl'].sum() * 100:.2f}")

    except Exception as e:
        print(f'Error: {e}')

run_backtest()
