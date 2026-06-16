import pandas as pd
import numpy as np
from datetime import datetime, timedelta

def run_audit():
    try:
        # 1. Load book ticks (30MB, high res)
        # Columns: timestamp, asset_id, bid, ask, bid_sz, ask_sz, type
        df_ticks = pd.read_csv('logs/book_events.csv', names=['ts', 'asset_id', 'bid', 'ask', 'bsz', 'asz', 'type'])
        df_ticks['ts'] = pd.to_datetime(df_ticks['ts'])
        
        # 2. Load combat signals
        df_sigs = pd.read_csv('logs/signals.csv')
        combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
        sigs = df_sigs[df_sigs['event_type'].isin(combat_types)].copy()
        sigs['ts'] = pd.to_datetime(sigs['timestamp_utc'])

        print(f"Replaying {len(sigs)} combat signals against tick data...")

        results = []
        for _, sig in sigs.iterrows():
            token_id = str(sig['yes_token_id'])
            entry_ts = sig['ts']
            # Using anchor_price or current_price as the cost basis
            entry_px = sig['ask']
            if pd.isna(entry_px): continue
            
            # Find all ticks for this token in the 30s window after signal
            window_end = entry_ts + timedelta(seconds=30)
            token_ticks = df_ticks[
                (df_ticks['asset_id'] == token_id) & 
                (df_ticks['ts'] >= entry_ts) & 
                (df_ticks['ts'] <= window_end)
            ].sort_values('ts')
            
            if token_ticks.empty: continue
            
            # Simulated Trailing Stop (High Res)
            peak_bid = 0
            armed = False
            trail_pnl = None
            trail_status = 'horizon_30s'
            
            for _, tick in token_ticks.iterrows():
                # We exit on the BID price
                bid = tick['bid']
                if pd.isna(bid): continue
                
                markout = bid - entry_px
                if markout > peak_bid:
                    peak_bid = markout
                
                # Arm at +1c profit
                if peak_bid >= 0.01:
                    armed = True
                
                # If armed, exit on 1c drop from peak
                if armed and markout <= (peak_bid - 0.01):
                    trail_pnl = markout
                    trail_status = 'trail_exit'
                    break
            
            # If never trailed, use the final tick in window
            if trail_pnl is None:
                trail_pnl = token_ticks.iloc[-1]['bid'] - entry_px
            
            results.append({
                'match': sig['market_name'][:30],
                'trail_pnl': trail_pnl,
                'status': trail_status,
                'final_30s_pnl': token_ticks.iloc[-1]['bid'] - entry_px,
                'max_possible': peak_bid
            })

        res_df = pd.DataFrame(results)
        print("\n=== HIGH-RESOLUTION TICK REPLAY RESULTS ===")
        print(f"Avg 1c Trailing Stop PnL: {res_df['trail_pnl'].mean():+.4f}")
        print(f"Avg Fixed 30s Horizon PnL: {res_df['final_30s_pnl'].mean():+.4f}")
        print(f"Avg Max Possible (Peak):   {res_df['max_possible'].mean():+.4f}")
        
        print("\nExit Status Distribution:")
        print(res_df['status'].value_counts())
        
        print(f"\nTotal Proj Alpha Gain vs Fixed: {((res_df['trail_pnl'].mean() - res_df['final_30s_pnl'].mean()) * 100):.2f}%")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    run_audit()
