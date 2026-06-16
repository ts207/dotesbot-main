import pandas as pd
import numpy as np
from datetime import datetime, timedelta

def analyze():
    try:
        # 1. Load signals
        print("Loading signals...")
        df_sigs = pd.read_csv('logs/signals.csv')
        combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
        sigs = df_sigs[df_sigs['event_type'].isin(combat_types)].copy()
        
        # 2. Load high-res book ticks to find prices at specific offsets
        print("Loading book ticks...")
        df_ticks = pd.read_csv('logs/book_events.csv', header=0)
        df_ticks['ts'] = pd.to_datetime(df_ticks['timestamp_utc'])
        df_ticks['asset_id'] = df_ticks['asset_id'].astype(str)

        results = []
        for _, sig in sigs.iterrows():
            token_id = str(sig.get('yes_token_id') or sig.get('token_id'))
            signal_ts = pd.to_datetime(sig['timestamp_utc'])
            steam_age_s = float(sig['steam_age_ms']) / 1000.0
            
            # The "Actual Event Time" is approximately signal_ts - steam_age_s
            event_ts = signal_ts - timedelta(seconds=steam_age_s)
            
            # Find price at Event Time (Start of the move)
            t_event = df_ticks[(df_ticks['asset_id'] == token_id) & (df_ticks['ts'] <= event_ts)].sort_values('ts').tail(1)
            # Find price at Signal Time (Snapshot Receipt)
            t_signal = df_ticks[(df_ticks['asset_id'] == token_id) & (df_ticks['ts'] <= signal_ts)].sort_values('ts').tail(1)
            
            if t_event.empty or t_signal.empty: continue
            
            px_start = (t_event.iloc[0]['best_bid'] + t_event.iloc[0]['best_ask']) / 2
            px_signal = (t_signal.iloc[0]['best_bid'] + t_signal.iloc[0]['best_ask']) / 2
            
            # Move before we even saw the snapshot
            move_before = px_signal - px_start
            
            # Move 30s after the signal (The drift we try to capture)
            window_end = signal_ts + timedelta(seconds=30)
            t_end = df_ticks[(df_ticks['asset_id'] == token_id) & (df_ticks['ts'] <= window_end)].sort_values('ts').tail(1)
            
            if t_end.empty: continue
            px_end = (t_end.iloc[0]['best_bid'] + t_end.iloc[0]['best_ask']) / 2
            move_after = px_end - px_signal
            
            results.append({
                'event_type': sig['event_type'],
                'steam_age_s': steam_age_s,
                'move_before_receipt': move_before,
                'move_after_receipt': move_after
            })

        res_df = pd.DataFrame(results)
        print("\n=== MARKET REACTION BEFORE & AFTER SNAPSHOT RECEIPT ===")
        print(res_df.groupby('event_type').agg({
            'steam_age_s': 'mean',
            'move_before_receipt': 'mean',
            'move_after_receipt': 'mean'
        }))

    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    analyze()
