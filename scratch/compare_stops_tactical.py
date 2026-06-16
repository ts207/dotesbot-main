import pandas as pd
import numpy as np

def run_compare():
    try:
        df = pd.read_csv('logs/signal_markouts.csv')
        combat = df[df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])].copy()
        
        results = []
        for stop_val in [None, 0.01, 0.03, 0.05, 0.10]:
            total_pnl = 0
            wins = 0
            
            for _, row in combat.iterrows():
                m3, m10, m30 = row['markout_3s'], row['markout_10s'], row['markout_30s']
                
                trade_pnl = m30 # Default: hold to 30s
                
                if stop_val is not None:
                    # Check for stop-out at each horizon
                    for markout in [m3, m10, m30]:
                        if markout <= -stop_val:
                            trade_pnl = markout
                            break
                
                total_pnl += trade_pnl
                if trade_pnl > 0: wins += 1
            
            label = f"Stop: {stop_val if stop_val else 'None':<5}"
            avg = total_pnl / len(combat)
            print(f"{label} | WinRate: {wins/len(combat):.1%} | Avg PnL: {avg:+.4f} | Total ($100 stake): ${total_pnl*100:+.2f}")

    except Exception as e:
        print(f'Error: {e}')

run_compare()
