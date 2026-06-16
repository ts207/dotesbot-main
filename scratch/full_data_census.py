import pandas as pd
import glob
import numpy as np

def run_census():
    print("=== FULL DATA CENSUS (ALL TOURNAMENTS) ===")
    
    # 1. RAW MATCHES (Snapshots)
    raw_files = glob.glob('logs/raw_snapshots.csv*')
    all_raw_mids = set()
    for f in raw_files:
        try:
            # Match ID is the 3rd column
            df = pd.read_csv(f, usecols=[2], names=['match_id'], header=0, on_bad_lines='skip')
            all_raw_mids.update(df['match_id'].unique())
        except: pass
    print(f"1. Total Matches in Raw Snapshots: {len(all_raw_mids)}")

    # 2. MATCHES WITH EVENTS (Dota Events)
    ev_files = glob.glob('logs/dota_events.csv*')
    all_ev_mids = set()
    for f in ev_files:
        try:
            df = pd.read_csv(f, usecols=[0], names=['match_id'], header=0, on_bad_lines='skip')
            all_ev_mids.update(df['match_id'].unique())
        except: pass
    print(f"2. Matches with Detected Events:  {len(all_ev_mids)}")

    # 3. MATCHES WITH SIGNALS (Signals)
    sig_files = glob.glob('logs/signals.csv*')
    all_sig_mids = set()
    for f in sig_files:
        try:
            df = pd.read_csv(f, usecols=[4], names=['match_id'], header=0, on_bad_lines='skip')
            all_sig_mids.update(df['match_id'].unique())
        except: pass
    print(f"3. Matches that Fired Signals:    {len(all_sig_mids)}")

    # 4. MATCHES WITH TRADES (Combat Sniper)
    try:
        df_shadow = pd.read_csv('logs/shadow_trades.csv')
        # Total historical shadow trades (combat events passing basic logic)
        combat_trades = df_shadow[df_shadow['event_type'].isin(['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP'])]
        trade_mids = set(combat_trades['match_id'].unique())
        print(f"4. Matches with Combat Trades:    {len(trade_mids)}")
    except: pass

    print("\n--- Why the 'Yield' is low ---")
    print(f"Capture Rate (Signals / Snapshots): {len(all_sig_mids)/len(all_raw_mids):.1%}")
    print(f"Conversion Rate (Trades / Signals): {len(trade_mids)/len(all_sig_mids):.1%}")

run_census()
