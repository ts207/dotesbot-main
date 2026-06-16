import pandas as pd
import numpy as np
import glob

def run_backtest():
    try:
        # 1. Aggregate ALL historical signals
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
        df_mark = df_mark.rename(columns={'signal_timestamp_utc': 'timestamp_utc'})
        
        # Unique keys for merging
        df_sig = df_sig.drop_duplicates(subset=['timestamp_utc', 'match_id'])
        df_mark = df_mark.drop_duplicates(subset=['timestamp_utc', 'match_id'])
        
        # 3. Merge
        merged = pd.merge(df_sig, df_mark[['timestamp_utc', 'match_id', 'markout_30s']], 
                         on=['timestamp_utc', 'match_id'], how='inner')
        
        # 4. Filter for Combat Only
        combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
        merged = merged[merged['event_type'].isin(combat_types)].copy()
        
        # Numeric coerce
        merged['steam_age_ms'] = pd.to_numeric(merged['steam_age_ms'], errors='coerce')
        merged['ask'] = pd.to_numeric(merged['ask'], errors='coerce')
        merged = merged.dropna(subset=['steam_age_ms', 'ask', 'markout_30s'])

        print("=== BACKTEST: ALL HISTORICAL DATA WITH NEW GATES (15s / 90c) ===")
        print(f"Total Combat Signals analyzed: {len(merged)}")

        # A. OLD LOGIC: 3s Gate, 82c Cap
        old_trades = merged[(merged['steam_age_ms'] <= 3000) & (merged['ask'] <= 0.82)]
        
        # B. NEW LOGIC: 15s Gate, 90c Cap
        new_trades = merged[(merged['steam_age_ms'] <= 15000) & (merged['ask'] <= 0.90)]

        for label, subset in [("Old Logic (3s/82c)", old_trades), ("New Logic (15s/90c)", new_trades)]:
            print(f"\n{label}:")
            print(f"  Total Trades:  {len(subset)}")
            if len(subset) > 0:
                print(f"  Win Rate (30s): {(subset['markout_30s'] > 0).mean():.1%}")
                print(f"  Mean Alpha:    {subset['markout_30s'].mean():+.4f}c")
                print(f"  Total Proj $:  ${subset['markout_30s'].sum() * 100:.2f} (per $100 stake)")
                print(f"  Unique Matches: {subset['match_id'].nunique()}")

    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_audit = run_backtest()
