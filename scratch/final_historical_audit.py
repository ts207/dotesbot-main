import pandas as pd
import glob
import numpy as np

def run_audit():
    # 1. Load and Aggregate ALL signal logs
    sig_files = ['logs/signals.csv'] + glob.glob('logs/signals.csv*.bak')
    all_sigs = []
    cols = pd.read_csv('logs/signals.csv', nrows=0).columns
    for f in sig_files:
        try:
            df = pd.read_csv(f, names=cols, on_bad_lines='skip') if 'bak' in f else pd.read_csv(f, on_bad_lines='skip')
            all_sigs.append(df)
        except: pass
    df_sig = pd.concat(all_sigs, ignore_index=True)
    
    # 2. Load Markouts
    df_mark = pd.read_csv('logs/signal_markouts.csv')
    df_mark = df_mark.drop_duplicates(subset=['signal_timestamp_utc', 'match_id'])
    
    # 3. Merge on unique keys
    merged = pd.merge(df_sig, df_mark[['signal_timestamp_utc', 'match_id', 'markout_30s', 'markout_10s', 'markout_3s']], 
                     left_on=['timestamp_utc', 'match_id'], 
                     right_on=['signal_timestamp_utc', 'match_id'], 
                     how='inner')
    
    # 4. Apply the exact 2-Event "Gold Standard" Portfolio Filters
    combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
    portfolio = merged[
        (merged['event_type'].isin(combat_types)) &
        (pd.to_numeric(merged['ask'], errors='coerce') <= 0.90) &
        (pd.to_numeric(merged['steam_age_ms'], errors='coerce') <= 15000) &
        (pd.to_numeric(merged['executable_edge'], errors='coerce') >= 0.001)
    ].copy()

    print(f"=== FINAL AUDIT: Combat Sniper Strategy (n={len(portfolio)}) ===")
    print(f"Dataset: All History (Rotated + Current)")
    
    # Performance Stats
    horizons = ['markout_3s', 'markout_10s', 'markout_30s']
    perf = portfolio[horizons].agg(['mean', 'median'])
    win_r = (portfolio['markout_30s'] > 0).mean()
    
    print("\nMean Alpha Curve (cents):")
    print(perf.loc['mean'])
    print(f"\nWin Rate (30s): {win_r:.1%}")
    
    # Projected PnL
    avg_m30 = portfolio['markout_30s'].mean()
    print(f"Avg PnL per trade: {avg_m30:+.4f}c")
    print(f"Total Proj PnL ($100 stake/trade): ${len(portfolio) * avg_m30 * 100:+.2f}")

    # Risk Metrics
    print(f"\nMax Drawdown (30s): {portfolio['markout_30s'].min():.4f}c")
    print(f"Max Run-up (30s):   {portfolio['markout_30s'].max():.4f}c")

run_audit()
