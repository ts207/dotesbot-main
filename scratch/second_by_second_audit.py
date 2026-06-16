import pandas as pd
import numpy as np
from datetime import datetime, timedelta

def run_audit():
    try:
        # 1. Load book ticks (high resolution raw data)
        print("Loading high-res book ticks...")
        df_ticks = pd.read_csv('logs/book_events.csv', header=0)
        df_ticks['ts'] = pd.to_datetime(df_ticks['timestamp_utc'])
        
        # 2. Load combat signals
        print("Loading signal history...")
        df_sigs = pd.read_csv('logs/signals.csv')
        combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
        sigs = df_sigs[df_sigs['event_type'].isin(combat_types)].copy()
        sigs['ts'] = pd.to_datetime(sigs['timestamp_utc'])

        print(f"Replaying {len(sigs)} signals at 1-second resolution...")

        results = []
        for _, sig in sigs.iterrows():
            token_id = str(sig.get('yes_token_id') or sig.get('token_id'))
            entry_ts = sig['ts']
            entry_px = float(sig['ask']) if not pd.isna(sig['ask']) else None
            if not entry_px: continue
            
            # Find ticks for this token in the 60s window
            window_end = entry_ts + timedelta(seconds=60)
            token_ticks = df_ticks[
                (df_ticks['asset_id'].astype(str) == token_id) & 
                (df_ticks['ts'] >= entry_ts) & 
                (df_ticks['ts'] <= window_end)
            ].sort_values('ts')
            
            if token_ticks.empty: continue

            # Resample to 1-second buckets to see the "Repricing Pulse"
            token_ticks.set_index('ts', inplace=True)
            resampled = token_ticks['best_bid'].resample('1S').last().ffill()
            
            # Calculate markout at each second relative to entry
            markouts = resampled - entry_px
            
            results.append(markouts.values[:60]) # Ensure exactly 60 seconds

        # Create a heatmap/matrix of the repricing pulse
        # Padd with NaNs if lengths differ
        max_len = 60
        matrix = [np.pad(r, (0, max_len - len(r)), constant_values=np.nan) for r in results]
        df_pulse = pd.DataFrame(matrix)

        print("\n=== SECOND-BY-SECOND REPRICING PULSE (ALL DATA) ===")
        print(f"{'Second':<8} | {'Avg Markout':<12} | {'Win Rate':<10}")
        print("-" * 40)
        
        # Display key intervals
        for sec in [1, 3, 5, 10, 15, 20, 25, 30, 45, 60]:
            if sec < len(df_pulse.columns):
                mean_m = df_pulse[sec].mean()
                win_r = (df_pulse[sec] > 0).mean()
                print(f"{sec:>2}s       | {mean_m:>+11.4f}c | {win_r:>8.1%}")

        # Find the mathematical peak
        means = df_pulse.mean()
        peak_sec = means.idxmax()
        print(f"\nMATHEMATICAL PEAK: {peak_sec}s (Avg: {means[peak_sec]:+.4f}c)")

    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_audit()
