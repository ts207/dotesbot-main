import pandas as pd
import numpy as np

def run_backtest():
    try:
        df = pd.read_csv('logs/signal_markouts.csv')
        combat = df[df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])].copy()
        
        # Scenario: TP at +3c, SL at -5c, all within the 30s window.
        # Logic: If neither is hit by 30s, we exit at the 30s price (m30).
        
        results = []
        for idx, row in combat.iterrows():
            m3, m10, m30 = row['markout_3s'], row['markout_10s'], row['markout_30s']
            
            pnl = m30 # Default: exit at 30s price
            status = 'exit_at_30s'
            
            # Sequence simulation: 3s -> 10s -> 30s
            # Note: m30 is the final check. If SL/TP not hit by then, we get m30.
            for time_step, markout in [('3s', m3), ('10s', m10), ('30s', m30)]:
                # 1. Check Take Profit +3c
                if markout >= 0.03:
                    pnl = 0.03
                    status = f'tp_hit_{time_step}'
                    break
                # 2. Check Stop Loss -5c
                if markout <= -0.05:
                    pnl = -0.05 # or markout if it gapped past -0.05
                    pnl = min(-0.05, markout) 
                    status = f'sl_hit_{time_step}'
                    break
            
            results.append({'pnl': pnl, 'status': status})
            
        res_df = pd.DataFrame(results)
        print(f"=== 30s Window: TP (+3c) vs SL (-5c) (n={len(combat)}) ===")
        print(res_df['status'].value_counts())
        print(f"\nAvg PnL per trade: {res_df['pnl'].mean():+.4f}")
        print(f"Win Rate (TP hit): {(res_df['pnl'] > 0).mean():.1%}")
        print(f"Loss Rate (SL hit or M30 < 0): {(res_df['pnl'] < 0).mean():.1%}")
        print(f"Total Proj PnL ($100 Stake): ${res_df['pnl'].sum() * 100:.2f}")

    except Exception as e:
        print(f'Error: {e}')

run_backtest()
