import pandas as pd
import glob
import numpy as np

def run_audit():
    # 1. Load Shadow Trades - it's the most reliable historical record for this analysis
    # because it already contains: event_type, entry_price, lag, markout_30s.
    try:
        df = pd.read_csv('logs/shadow_trades.csv')
    except Exception as e:
        print(f"Error loading shadow trades: {e}")
        return

    # 2. Filter for Combat Only + Portfolio Logic
    combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
    
    # Logic: Combat Events + 15s lag + 90c entry
    # (Shadow 'lag' is in seconds)
    portfolio = df[
        (df['event_type'].isin(combat_types)) &
        (df['entry_price'] <= 0.90) &
        (df['lag'] <= 15.0)
    ].copy()

    # Clean markouts
    portfolio = portfolio.dropna(subset=['markout_30s'])

    print(f"=== DEFINITIVE AUDIT: Combat Sniper (All History, n={len(portfolio)}) ===")
    
    # 3. Performance Stats
    mean_m30 = portfolio['markout_30s'].mean()
    win_r30 = (portfolio['markout_30s'] > 0).mean()
    
    print(f"Mean Markout (30s): {mean_m30:+.4f}c")
    print(f"Win Rate (30s):     {win_r30:.1%}")
    
    # Use real cash profit metric ($100 stake)
    total_pnl = portfolio['markout_30s'].sum() * 100
    print(f"Total Proj PnL ($100 stake): ${total_pnl:+.2f}")
    
    # 4. Individual Event Breakdown
    print("\n--- Performance by Event Type ---")
    print(portfolio.groupby('event_type')['markout_30s'].agg(['count', 'mean']))

    # 5. Risk: Worst Losers
    print("\n--- Worst Reversals (Max Loss at 30s) ---")
    print(portfolio[['market_name', 'markout_30s']].sort_values('markout_30s').head(5).to_string(index=False))

if __name__ == "__main__":
    run_audit()
