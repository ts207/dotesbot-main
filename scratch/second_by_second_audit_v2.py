import pandas as pd
import numpy as np
import glob
from datetime import datetime, timedelta

def run_audit():
    try:
        # 1. Load book ticks
        print("Loading high-res book ticks...")
        df_ticks = pd.read_csv('logs/book_events.csv', header=0)
        df_ticks['ts'] = pd.to_datetime(df_ticks['timestamp_utc'])
        
        # 2. Load and aggregate ALL signals
        print("Loading aggregated signals...")
        sig_files = ['logs/signals.csv'] + glob.glob('logs/signals.csv*.bak')
        all_sigs = []
        cols = pd.read_csv('logs/signals.csv', nrows=0).columns
        for f in sig_files:
            try:
                df = pd.read_csv(f, names=cols, on_bad_lines='skip') if 'bak' in f else pd.read_csv(f, on_bad_lines='skip')
                all_sigs.append(df)
            except: pass
        
        df_sig = pd.concat(all_sigs, ignore_index=True)
        combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
        sigs = df_sig[df_sig['event_type'].isin(combat_types)].copy()
        sigs['ts'] = pd.to_datetime(sigs['timestamp_utc'])
        
        # Numeric coerce
        sigs['ask'] = pd.to_numeric(sigs['ask'], errors='coerce')
        sigs = sigs.dropna(subset=['ts', 'ask'])

        print(f"Replaying {len(sigs)} combat signals at 1-second resolution...")

        results = []
        for _, sig in sigs.iterrows():
            token_id = str(sig.get('yes_token_id') or sig.get('token_id'))
            entry_ts = sig['ts']
            entry_px = float(sig['ask'])
            
            # 60s window
            window_end = entry_ts + timedelta(seconds=60)
            token_ticks = df_ticks[
                (df_ticks['asset_id'].astype(str) == token_id) & 
                (df_ticks['ts'] >= entry_ts) & 
                (df_ticks['ts'] <= window_end)
            ].sort_values('ts')
            
            if token_ticks.empty: continue

            # Resample to 1S buckets
            token_ticks = token_ticks.set_index('ts')
            # Use 'best_bid' as our exit price
            resampled = token_ticks['best_bid'].resample('1S').last().ffill()
            
            # Calculate markouts
            markouts = resampled - entry_px
            # Ensure we start at T=0
            markouts.index = (markouts.index - entry_ts).total_seconds().astype(int)
            
            # Map into a dict for easy dataframe conversion
            results.append(markouts.to_dict())

        # Build final pulse dataframe
        df_pulse = pd.DataFrame(results).reindex(columns=range(61))

        print("\n=== SECOND-BY-SECOND REPRICING PULSE (ALL DATA) ===")
        print(f"{'Second':<8} | {'Avg Markout':<12} | {'Win Rate':<10}")
        print("-" * 40)
        
        # Display key intervals
        for sec in [1, 2, 3, 5, 10, 15, 20, 25, 30, 45, 60]:
            col_data = df_pulse[sec].dropna()
            if len(col_data) > 0:
                mean_m = col_data.mean()
                win_r = (col_data > 0).mean()
                print(f"{sec:>2}s       | {mean_m:>+11.4f}c | {win_r:>8.1%}")

        # Mathematical Peak search
        pulse_means = df_pulse.mean()
        peak_sec = pulse_means.idxmax()
        print(f"\nMATHEMATICAL PEAK: {peak_sec}s (Avg: {pulse_means[peak_sec]:+.4f}c)")
        
        print(f"\nYield (Signals with tick coverage): {len(df_pulse)}")

    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_audit()
