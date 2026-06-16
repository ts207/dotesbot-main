import pandas as pd
import numpy as np

def run_backtest():
    try:
        df = pd.read_csv('logs/signal_markouts.csv')
        combat = df[df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])].copy()
        
        results = []
        for idx, row in combat.iterrows():
            m3, m10, m30 = row['markout_3s'], row['markout_10s'], row['markout_30s']
            
            pnl = m30 
            status = 'horizon_30s'
            peak = 0
            
            # TRAILING STOP LOGIC:
            # 1. As soon as price crosses +1c profit, "ARM" the trail.
            # 2. Once ARMED, if price drops 1c from its peak, EXIT.
            
            armed = False
            for step, val in [('3s', m3), ('10s', m10), ('30s', m30)]:
                if val > peak: peak = val
                if peak >= 0.01: armed = True
                
                if armed and val <= (peak - 0.01):
                    pnl = val
                    status = f'trail_exit_{step}'
                    break
                    
            results.append({'pnl': pnl})
            
        res_df = pd.DataFrame(results)
        print(f"Avg PnL: {res_df['pnl'].mean():+.4f}")
    except: pass

run_backtest()
