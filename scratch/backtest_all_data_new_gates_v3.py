import pandas as pd
import numpy as np
import glob

def run_backtest():
    try:
        # 1. Load Shadow Trades - it's already the result of an exhaustive internal backtest
        # It contains all the necessary columns: event_type, lag, entry_price, markout_30s, match_id
        df = pd.read_csv('logs/shadow_trades.csv')
        
        # 2. Filter for Combat Only
        combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
        df = df[df['event_type'].isin(combat_types)].copy()
        
        # Numeric coerce
        df['lag'] = pd.to_numeric(df['lag'], errors='coerce')
        df['entry_price'] = pd.to_numeric(df['entry_price'], errors='coerce')
        df['markout_30s'] = pd.to_numeric(df['markout_30s'], errors='coerce')
        df = df.dropna(subset=['lag', 'entry_price', 'markout_30s'])

        print("=== FINAL BACKTEST: ALL HISTORICAL DATA WITH NEW GATES (15s / 90c) ===")
        print(f"Total historical combat signals available: {len(df)}")

        # A. OLD LOGIC: 3s Gate, 82c Cap
        old_trades = df[(df['lag'] <= 3.0) & (df['entry_price'] <= 0.82)]
        
        # B. NEW LOGIC: 15s Gate, 90c Cap
        new_trades = df[(df['lag'] <= 15.0) & (df['entry_price'] <= 0.90)]

        for label, subset in [("Old Logic (3s/82c)", old_trades), ("New Logic (15s/90c)", new_trades)]:
            print(f"\n{label}:")
            print(f"  Total Trades:  {len(subset)}")
            if len(subset) > 0:
                print(f"  Win Rate (30s): {(subset['markout_30s'] > 0).mean():.1%}")
                print(f"  Mean Alpha:    {subset['markout_30s'].mean():+.4f}c")
                print(f"  Total Proj $:  ${subset['markout_30s'].sum() * 100:.2f} (per $100 stake)")
                print(f"  Matches Traded: {subset['match_id'].nunique()}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    run_backtest()
