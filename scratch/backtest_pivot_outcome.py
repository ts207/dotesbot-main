import pandas as pd
import numpy as np

def run_backtest():
    try:
        # 1. Load Shadow Trades - this has the 'radiant_win' label and 'event_direction'
        # which lets us know if the signal was actually right.
        df = pd.read_csv('logs/shadow_trades.csv')
        # Filter for combat only and events that have a markout_30s
        combat = df[df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])].copy()
        combat = combat.dropna(subset=['markout_30s'])
        
        # Determine if signal was correct at settlement
        # radiant_win is 1.0 or 0.0. 
        # If event_direction == 'radiant' and radiant_win == 1.0 -> WIN
        # If event_direction == 'dire' and radiant_win == 0.0 -> WIN
        def get_settle_pnl(row):
            entry = row['entry_price']
            win = (row['event_direction'] == 'radiant' and row['radiant_win'] == 1.0) or \
                  (row['event_direction'] == 'dire' and row['radiant_win'] == 0.0)
            return (1.0 - entry) if win else (0.0 - entry)

        results = []
        for idx, row in combat.iterrows():
            m3, m10, m30 = row['markout_3s'], row['markout_10s'], row['markout_30s']
            
            pnl_c = None
            
            # Stage 1: Early TP (+3c)
            if m3 >= 0.03: pnl_c = 0.03
            elif m10 >= 0.03: pnl_c = 0.03
            
            # Stage 2: Tactical 30s Profit
            if pnl_c is None and m30 > 0:
                pnl_c = m30
                
            # Stage 3: Pivot to Settlement
            if pnl_c is None:
                pnl_c = get_settle_pnl(row)
            
            results.append(pnl_c)
            
        print(f"=== Ground-Truth Pivot Backtest (n={len(combat)}) ===")
        print(f"Avg PnL per signal: {np.mean(results):+.4f}")
        print(f"Total Proj PnL ($100 stake proxy): ${np.sum(results) * 100:.2f}")

    except Exception as e:
        print(f"Error: {e}")

run_backtest()
