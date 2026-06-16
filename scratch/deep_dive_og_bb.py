import pandas as pd
import numpy as np
from datetime import datetime, timedelta

def analyze():
    match_id = "8828557643"
    print(f"=== DEEP DIVE: OG vs BetBoom (ID: {match_id}) ===")
    
    # 1. Signals for this match
    df_sig = pd.read_csv('logs/signals.csv')
    match_sigs = df_sig[df_sig['match_id'].astype(str) == match_id].copy()
    match_sigs['game_time_min'] = match_sigs['game_time_sec'] / 60.0
    
    print(f"\nSignals Fired: {len(match_sigs)}")
    print(match_sigs['decision'].value_counts())
    
    # 2. Replay the timeline
    print("\n--- Event Timeline ---")
    cols = ['game_time_min', 'event_type', 'decision', 'skip_reason', 'ask', 'executable_edge', 'steam_age_ms']
    print(match_sigs[cols].sort_values('game_time_min').to_string(index=False))

    # 3. Analyze the TRADE (paper_buy_yes)
    trade_sig = match_sigs[match_sigs['decision'] == 'paper_buy_yes'].iloc[0]
    ts = trade_sig['timestamp_utc']
    
    print("\n--- Trade Detail (at T=22.4m) ---")
    print(f"Event:    {trade_sig['event_type']}")
    print(f"Price:    {trade_sig['ask']}")
    print(f"Edge:     {trade_sig['executable_edge']}")
    print(f"Steam Age: {trade_sig['steam_age_ms']}ms")
    
    # 4. Find the Markouts for this specific trade
    try:
        df_mark = pd.read_csv('logs/signal_markouts.csv')
        # Join on timestamp string (might need exact match)
        markout = df_mark[df_mark['signal_timestamp_utc'] == ts]
        if not markout.empty:
            print("\n--- Realized Markouts ---")
            print(f"3s:  {markout.iloc[0]['markout_3s']:+.4f}")
            print(f"10s: {markout.iloc[0]['markout_10s']:+.4f}")
            print(f"30s: {markout.iloc[0]['markout_30s']:+.4f}")
        else:
            print("\nNo markout record found for this signal timestamp.")
    except: pass

if __name__ == "__main__":
    analyze()
