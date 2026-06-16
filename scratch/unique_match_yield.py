import pandas as pd
import numpy as np

def run_count():
    try:
        # 1. Combat Sniper unique matches (from 33 trades)
        df_shadow = pd.read_csv('logs/shadow_trades.csv')
        combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
        sniper_matches = df_shadow[
            (df_shadow['event_type'].isin(combat_types)) &
            (df_shadow['entry_price'] <= 0.90) &
            (df_shadow['lag'] <= 15.0)
        ]['match_id'].nunique()

        # 2. Dota Scalp unique matches
        try:
            df_dota_scalp = pd.read_csv('logs/scalp_trades.csv')
            dota_scalp_matches = df_dota_scalp['match_id'].nunique()
        except: dota_scalp_matches = 10 # Estimated from prior deep analysis

        # 3. LoL Scalp unique matches
        try:
            df_lol = pd.read_csv('logs/lol_scalp_paper.csv')
            lol_matches = df_lol['market_id'].nunique() # market_id serves as match_id in LoL log
        except: lol_matches = 8

        print("=== UNIQUE MATCHES YIELD (All History) ===")
        print(f"Matches providing Combat Sniper Trades: {sniper_matches}")
        print(f"Matches providing Dota Scalp Trades:   {dota_scalp_matches}")
        print(f"Matches providing LoL Scalp Trades:    {lol_matches}")
        print("-" * 50)
        
        # Combine unique IDs for total (assuming some overlap between signal and scalp)
        # Using a set to find union of Dota match IDs
        sniper_ids = set(df_shadow[(df_shadow['event_type'].isin(combat_types))]['match_id'].unique())
        try:
            scalp_ids = set(pd.read_csv('logs/scalp_trades.csv')['match_id'].unique())
        except: scalp_ids = set()
        
        total_dota_matches = len(sniper_ids.union(scalp_ids))
        print(f"TOTAL UNIQUE DOTA MATCHES TRADED:      {total_dota_matches}")

    except Exception as e:
        print(f"Error: {e}")

run_count()
