import pandas as pd
import numpy as np

def run_backtest():
    try:
        df = pd.read_csv('logs/signal_markouts.csv')
        combat = df[df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])].copy()
        
        results = []
        for idx, row in combat.iterrows():
            m3, m10, m30 = row['markout_3s'], row['markout_10s'], row['markout_30s']
            
            # SPLIT EXIT LOGIC:
            # 50% of position exits at the 3s local peak
            # 50% of position exits at the 30s horizon
            
            pnl_3s = m3
            pnl_30s = m30
            
            combined_pnl = (pnl_3s + pnl_30s) / 2
            
            results.append({'pnl': combined_pnl})
            
        res_df = pd.DataFrame(results)
        print("=== Split Exit Backtest (50% @ 3s, 50% @ 30s) ===")
        print(f"Avg PnL per trade: {res_df['pnl'].mean():+.4f}")
        print(f"Total Proj PnL ($100 Stake): ${res_df['pnl'].sum() * 100:.2f}")

    except Exception as e:
        print(f'Error: {e}')

run_backtest()
