import pandas as pd
import numpy as np
import glob
from datetime import datetime, timedelta

def analyze():
    try:
        print("Loading consolidated data...")
        # Load ALL signals including backups
        sig_files = ['logs/signals.csv'] + glob.glob('logs/signals.csv*.bak')
        all_sigs = []
        cols = pd.read_csv('logs/signals.csv', nrows=0).columns
        for f in sig_files:
            try:
                df = pd.read_csv(f, names=cols, on_bad_lines='skip') if 'bak' in f else pd.read_csv(f, on_bad_lines='skip')
                all_sigs.append(df)
            except: pass
        
        df_sig = pd.concat(all_sigs, ignore_index=True)
        df_sig['steam_age_ms'] = pd.to_numeric(df_sig['steam_age_ms'], errors='coerce')
        df_sig = df_sig.dropna(subset=['steam_age_ms'])
        
        combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
        sigs = df_sig[df_sig['event_type'].isin(combat_types)].copy()
        sigs['ts'] = pd.to_datetime(sigs['timestamp_utc'])
        
        print("Loading book ticks...")
        df_ticks = pd.read_csv('logs/book_events.csv', header=0)
        df_ticks['ts'] = pd.to_datetime(df_ticks['timestamp_utc'])
        df_ticks['asset_id'] = df_ticks['asset_id'].astype(str)

        results = []
        for _, sig in sigs.iterrows():
            token_id = str(sig.get('yes_token_id') or sig.get('token_id'))
            signal_ts = sig['ts']
            steam_age_s = float(sig['steam_age_ms']) / 1000.0
            
            # The "Actual Event Time"
            event_ts = signal_ts - timedelta(seconds=steam_age_s)
            
            # Use query for speed
            t_event = df_ticks[(df_ticks['asset_id'] == token_id) & (df_ticks['ts'] <= event_ts)].sort_values('ts').tail(1)
            t_signal = df_ticks[(df_ticks['asset_id'] == token_id) & (df_ticks['ts'] <= signal_ts)].sort_values('ts').tail(1)
            
            if t_event.empty or t_signal.empty: continue
            
            px_start = (t_event.iloc[0]['best_bid'] + t_event.iloc[0]['best_ask']) / 2
            px_signal = (t_signal.iloc[0]['best_bid'] + t_signal.iloc[0]['best_ask']) / 2
            
            # Move while snapshot was "in transit"
            move_before = px_signal - px_start
            
            # Drift after we saw it
            window_end = signal_ts + timedelta(seconds=30)
            t_end = df_ticks[(df_ticks['asset_id'] == token_id) & (df_ticks['ts'] <= window_end)].sort_values('ts').tail(1)
            
            if not t_end.empty:
                px_end = (t_end.iloc[0]['best_bid'] + t_end.iloc[0]['best_ask']) / 2
                move_after = px_end - px_signal
                
                results.append({
                    'event_type': sig['event_type'],
                    'age': steam_age_s,
                    'move_before': move_before,
                    'move_after': move_after
                })

        res_df = pd.DataFrame(results)
        print("\n=== MARKET DRIFT ANALYSIS: EVENT TIME TO SIGNAL TIME ===")
        summary = res_df.groupby('event_type').agg({
            'age': 'mean',
            'move_before': 'mean',
            'move_after': 'mean'
        })
        print(summary)
        
        print("\nYield Analysis:")
        print(f"Signals replayed: {len(res_df)}")
        print(f"Move Before as % of Total: {(res_df['move_before'].mean() / (res_df['move_before'].mean() + res_df['move_after'].mean()) * 100):.1f}%")

    except Exception as e:
        import traceback
        traceback.print_exc()

analyze()
