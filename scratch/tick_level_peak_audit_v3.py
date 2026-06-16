import pandas as pd
import numpy as np
from datetime import datetime, timedelta

def run_audit():
    try:
        # 1. Load book ticks - use actual headers
        print("Loading book ticks...")
        df_ticks = pd.read_csv('logs/book_events.csv', 
                             header=0,
                             usecols=['timestamp_utc', 'asset_id', 'best_bid', 'best_ask'])
        df_ticks['ts'] = pd.to_datetime(df_ticks['timestamp_utc'], errors='coerce')
        df_ticks = df_ticks.dropna(subset=['ts', 'best_bid'])
        
        # 2. Load signals
        print("Loading signals...")
        df_sigs = pd.read_csv('logs/signals.csv')
        combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
        sigs = df_sigs[df_sigs['event_type'].isin(combat_types)].copy()
        sigs['ts'] = pd.to_datetime(sigs['timestamp_utc'], errors='coerce')
        sigs = sigs.dropna(subset=['ts', 'ask'])

        print(f"Replaying {len(sigs)} combat signals against tick data...")

        results = []
        for _, sig in sigs.iterrows():
            token_id = str(sig['yes_token_id'])
            entry_ts = sig['ts']
            entry_px = float(sig['ask'])
            
            # Find all ticks for this token in the 30s window after signal
            window_end = entry_ts + timedelta(seconds=30)
            token_ticks = df_ticks[
                (df_ticks['asset_id'].astype(str) == token_id) & 
                (df_ticks['ts'] >= entry_ts) & 
                (df_ticks['ts'] <= window_end)
            ].sort_values('ts')
            
            if token_ticks.empty: continue
            
            # Simulated Trailing Stop (High Res)
            peak_bid_delta = -1.0 # start very low
            armed = False
            trail_pnl = None
            trail_status = 'horizon_30s'
            
            for _, tick in token_ticks.iterrows():
                bid = float(tick['best_bid'])
                markout = bid - entry_px
                
                if markout > peak_bid_delta:
                    peak_bid_delta = markout
                
                # Arm at +1c profit (clears technical spread cost)
                if peak_bid_delta >= 0.01:
                    armed = True
                
                # If armed, exit on 1c drop from peak
                if armed and markout <= (peak_bid_delta - 0.01):
                    trail_pnl = markout
                    trail_status = 'trail_exit'
                    break
            
            # If never trailed, use the final tick in window
            if trail_pnl is None:
                trail_pnl = float(token_ticks.iloc[-1]['best_bid']) - entry_px
            
            results.append({
                'trail_pnl': trail_pnl,
                'status': trail_status,
                'final_30s_pnl': float(token_ticks.iloc[-1]['best_bid']) - entry_px,
                'max_possible': peak_bid_delta
            })

        if not results:
            print("No signals matched any tick data in the window.")
            return

        res_df = pd.DataFrame(results)
        print("\n=== HIGH-RESOLUTION TICK REPLAY RESULTS ===")
        print(f"Sample Size: {len(res_df)} combat signals")
        print(f"Avg 1c Trailing Stop PnL: {res_df['trail_pnl'].mean():+.4f}")
        print(f"Avg Fixed 30s Horizon PnL: {res_df['final_30s_pnl'].mean():+.4f}")
        print(f"Avg Max Possible (Peak):   {res_df['max_possible'].mean():+.4f}")
        
        print("\nExit Status Distribution:")
        print(res_df['status'].value_counts())
        
        gain = res_df['trail_pnl'].mean() - res_df['final_30s_pnl'].mean()
        print(f"\nRealized Alpha Gain vs Fixed: {gain:+.4f} per trade")

    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_audit()
