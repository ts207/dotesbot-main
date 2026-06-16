import pandas as pd
import numpy as np
from datetime import datetime, timedelta

def run_audit():
    try:
        print("Loading book ticks...")
        df_ticks = pd.read_csv('logs/book_events.csv', header=0)
        df_ticks['ts'] = pd.to_datetime(df_ticks['timestamp_utc'])
        
        print("Loading signals...")
        df_sigs = pd.read_csv('logs/signals.csv')
        # Filter for ALL combat signals across ALL historical periods (including Stomps for comparison)
        combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP', 'POLL_RAPID_STOMP', 'POLL_DECISIVE_STOMP']
        sigs = df_sigs[df_sigs['event_type'].isin(combat_types)].copy()
        sigs['ts'] = pd.to_datetime(sigs['timestamp_utc'])

        print(f"Replaying {len(sigs)} total signals against tick data...")

        results = []
        for _, sig in sigs.iterrows():
            # Try both token_id and yes_token_id (signals.csv has many columns)
            token_id = str(sig.get('yes_token_id') or sig.get('token_id'))
            entry_ts = sig['ts']
            entry_px = float(sig['ask']) if not pd.isna(sig['ask']) else None
            if not entry_px: continue
            
            # Find all ticks for this token in the 30s window after signal
            window_end = entry_ts + timedelta(seconds=30)
            token_ticks = df_ticks[
                (df_ticks['asset_id'].astype(str) == token_id) & 
                (df_ticks['ts'] >= entry_ts) & 
                (df_ticks['ts'] <= window_end)
            ].sort_values('ts')
            
            if token_ticks.empty: continue
            
            peak_bid_delta = -1.0
            armed = False
            trail_pnl = None
            trail_status = 'horizon_30s'
            
            for _, tick in token_ticks.iterrows():
                bid = float(tick['best_bid'])
                if pd.isna(bid): continue
                markout = bid - entry_px
                
                if markout > peak_bid_delta:
                    peak_bid_delta = markout
                
                if peak_bid_delta >= 0.01:
                    armed = True
                
                if armed and markout <= (peak_bid_delta - 0.01):
                    trail_pnl = markout
                    trail_status = 'trail_exit'
                    break
            
            if trail_pnl is None:
                trail_pnl = float(token_ticks.iloc[-1]['best_bid']) - entry_px
            
            results.append({
                'event_type': sig['event_type'],
                'trail_pnl': trail_pnl,
                'status': trail_status,
                'final_30s_pnl': float(token_ticks.iloc[-1]['best_bid']) - entry_px,
                'max_possible': peak_bid_delta
            })

        if not results:
            print("No matches found. Checking token IDs in ticks vs signals...")
            print("Signal Token:", sigs['yes_token_id'].iloc[0])
            print("Tick Token Sample:", df_ticks['asset_id'].iloc[0])
            return

        res_df = pd.DataFrame(results)
        print("\n=== TICK REPLAY PERFORMANCE BY EVENT TYPE ===")
        summary = res_df.groupby('event_type').agg({
            'trail_pnl': 'mean',
            'final_30s_pnl': 'mean',
            'max_possible': 'mean'
        })
        print(summary)
        
        print("\nOverall Gain vs Fixed Horizon:")
        print(f"Trailing Stop Avg: {res_df['trail_pnl'].mean():+.4f}")
        print(f"Fixed 30s Avg:     {res_df['final_30s_pnl'].mean():+.4f}")
        print(f"Alpha Captured:    {res_df['trail_pnl'].mean() - res_df['final_30s_pnl'].mean():+.4f}")

    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_audit()
