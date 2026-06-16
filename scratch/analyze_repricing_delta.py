import pandas as pd
import numpy as np

def analyze():
    try:
        # Load raw markouts
        df = pd.read_csv('logs/signal_markouts.csv')
        # Combat only + <= 0.90
        combat = df[
            (df['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])) &
            (df['reference_ask'] <= 0.90)
        ].copy()
        
        # 1. Signals currently ACCEPTED (Repricing Filter PASSED)
        accepted = combat[combat['decision'] != 'skip']
        
        # 2. Signals currently REJECTED by repricing (RECOGNIZED BY SKIP REASON)
        # Note: We need to pull the skip reason from signals.csv to be 100% accurate,
        # but we can simulate based on markout_3s > 0.02 (fast move)
        repriced = combat[(combat['decision'] == 'skip') & (combat['markout_3s'] >= 0.02)]
        
        print(f"Accepted Signals: n={len(accepted)} | Mean M30={accepted['markout_30s'].mean():+.4f}")
        print(f"Repriced Signals: n={len(repriced)} | Mean M30={repriced['markout_30s'].mean():+.4f}")
        
        print("\n=== THE BREAKDOWN ===")
        sum_acc = accepted['markout_30s'].sum()
        sum_rep = repriced['markout_30s'].sum()
        print(f"PnL from Accepted: ${sum_acc*100:+.2f}")
        print(f"PnL from Repriced: ${sum_rep*100:+.2f}")
        print(f"TOTAL:             ${(sum_acc + sum_rep)*100:+.2f}")

    except Exception as e:
        print(e)

analyze()
