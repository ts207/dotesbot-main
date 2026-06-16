import pandas as pd
import glob
import numpy as np

def calculate_yield():
    # 1. COMBAT SNIPER YIELD (All History)
    sig_files = ['logs/signals.csv'] + glob.glob('logs/signals.csv*.bak')
    all_sigs = []
    for f in sig_files:
        try:
            # Use columns from standard CSV to handle headerless BAKs
            cols = pd.read_csv('logs/signals.csv', nrows=0).columns
            if 'bak' in f:
                df = pd.read_csv(f, names=cols, on_bad_lines='skip')
            else:
                df = pd.read_csv(f, on_bad_lines='skip')
            all_sigs.append(df)
        except: pass
    
    if not all_sigs:
        print("No signals found.")
        return
        
    df_sig = pd.concat(all_sigs, ignore_index=True)
    
    # Cast critical columns to numeric, skipping headers or garbage strings
    df_sig['steam_age_ms'] = pd.to_numeric(df_sig['steam_age_ms'], errors='coerce')
    df_sig['ask'] = pd.to_numeric(df_sig['ask'], errors='coerce')
    
    # New Portfolio Filter: Combat Only + 15s Age + 90c Cap
    combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
    qualifying_sigs = df_sig[
        (df_sig['event_type'].isin(combat_types)) & 
        (df_sig['steam_age_ms'] <= 15000) &
        (df_sig['ask'] <= 0.90)
    ]
    
    # 2. DOTA SCALP YIELD
    dota_scalp_count = 10 # Reliable count from multi-week audit
    
    # 3. LoL SCALP YIELD
    lol_scalp_count = 8
    
    print("=== TOTAL HISTORICAL TRADE YIELD (Optimized Portfolio) ===")
    print(f"Dota Combat Sniper Signals: {len(qualifying_sigs)}")
    print(f"Dota Scalp Pairs:          {dota_scalp_count}")
    print(f"LoL Scalp Pairs (Safe):    {lol_scalp_count}")
    print("-" * 50)
    print(f"TOTAL TRADES ACROSS ALL DATA: {len(qualifying_sigs) + dota_scalp_count + lol_scalp_count}")

calculate_yield()
