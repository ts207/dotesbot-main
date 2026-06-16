import pandas as pd
import numpy as np

def run_backtest():
    # 1. Load markouts (the price move truth)
    try:
        df_markouts = pd.read_csv('logs/signal_markouts.csv')
        print(f"Loaded {len(df_markouts)} markout records.")
    except Exception as e:
        print(f"Error loading markouts: {e}")
        return

    # 2. Filter for the two combat events
    combat_events = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
    df_combat = df_markouts[df_markouts['event_type'].isin(combat_events)].copy()
    
    if df_combat.empty:
        print("No combat events found in markouts.")
        return

    print(f"\nAnalyzing {len(df_combat)} Combat Signals Across All History:")
    
    # 3. Calculate Performance
    # Tactical (30s) assumes we bought at fair/mid and sold 30s later
    # Note: executable_edge in the log is at signal time.
    
    summary = df_combat.groupby('event_type').agg({
        'markout_3s': ['count', 'mean', 'median'],
        'markout_30s': ['mean', 'median'],
    })
    
    # Calculate Win Rate (markout > 0)
    def win_rate(x): return (x > 0).mean()
    
    wr_summary = df_combat.groupby('event_type').agg({
        'markout_30s': [win_rate]
    })
    
    print("\n--- PERFORMANCE BY EVENT TYPE (Tactical 30s) ---")
    print(summary)
    print("\nWin Rates (30s):")
    print(wr_summary)

    # 4. Projected PnL on $50 stake
    # PnL = markout * stake * multiplier (simplification)
    # Actually PnL $ = (markout / price) * stake. 
    # But since markout is already in 'cents' (delta), let's use a simpler proxy.
    # PnL USD ≈ (markout * 100) if stake was 100 shares.
    
    df_combat['pnl_30s'] = df_combat['markout_30s'] * 100 # Approx for $50-$100 stake
    
    print(f"\n--- CUMULATIVE PROJECTED PnL ($100 notional proxy) ---")
    print(df_combat.groupby('event_type')['pnl_30s'].sum())
    print(f"TOTAL COMBAT PnL: ${df_combat['pnl_30s'].sum():.2f}")

    # 5. Check if they were skipped or passed
    print("\n--- DATA COVERAGE (Signals that passed tactical filters) ---")
    print(df_combat['decision'].value_counts())

if __name__ == "__main__":
    run_backtest()
