import pandas as pd
import numpy as np

def run_backtest():
    try:
        # Load signals and markouts
        df_sig = pd.read_csv('logs/signals.csv')
        df_mark = pd.read_csv('logs/signal_markouts.csv')
        df_mark = df_mark.rename(columns={'signal_timestamp_utc': 'timestamp_utc'})
        
        # Merge
        merged = pd.merge(df_sig, df_mark[['timestamp_utc', 'match_id', 'markout_30s']], 
                         on=['timestamp_utc', 'match_id'], how='inner')
        
        # Numeric coerce
        merged['steam_age_ms'] = pd.to_numeric(merged['steam_age_ms'], errors='coerce')
        merged['ask'] = pd.to_numeric(merged['ask'], errors='coerce')
        merged['executable_edge'] = pd.to_numeric(merged['executable_edge'], errors='coerce')
        
        # COMBAT ONLY
        combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
        merged = merged[merged['event_type'].isin(combat_types)]

        print("=== BACKTEST: ALL DATA WITH NEW GATES (15s / 90c) ===")

        # 1. OLD LOGIC: 3s Gate, 82c Cap
        old_trades = merged[(merged['steam_age_ms'] <= 3000) & (merged['ask'] <= 0.82)]
        
        # 2. NEW LOGIC: 15s Gate, 90c Cap
        new_trades = merged[(merged['steam_age_ms'] <= 15000) & (merged['ask'] <= 0.90)]

        for label, subset in [("Old Logic (3s/82c)", old_trades), ("New Logic (15s/90c)", new_trades)]:
            print(f"\n{label}:")
            print(f"  Total Trades: {len(subset)}")
            if len(subset) > 0:
                print(f"  Win Rate:     {(subset['markout_30s'] > 0).mean():.1%}")
                print(f"  Mean Alpha:   {subset['markout_30s'].mean():+.4f}c")
                print(f"  Total Proj $: ${subset['markout_30s'].sum() * 100:.2f}")

        # 3. Match Coverage
        print(f"\nMatch Coverage (Unique Matches Traded):")
        print(f"  Old: {old_trades['match_id'].nunique()}")
        print(f"  New: {new_trades['match_id'].nunique()}")

    except Exception as e:
        print(f"Error: {e}")

run_backtest()
