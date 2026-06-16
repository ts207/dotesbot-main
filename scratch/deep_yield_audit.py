import pandas as pd
import glob
import numpy as np

def run_audit():
    print("=== DEEP YIELD AUDIT (All History) ===")
    
    # 1. Load EVERY signal fire ever recorded
    sig_files = glob.glob('logs/signals.csv*')
    all_sigs = []
    cols = pd.read_csv('logs/signals.csv', nrows=0).columns
    for f in sig_files:
        try:
            df = pd.read_csv(f, names=cols, on_bad_lines='skip', header=0) if 'bak' not in f else pd.read_csv(f, names=cols, on_bad_lines='skip')
            all_sigs.append(df)
        except: pass
    
    df_sig = pd.concat(all_sigs, ignore_index=True)
    df_sig['match_id'] = df_sig['match_id'].astype(str)
    
    # 2. Filter for our "Portfolio" Events only
    # These are the ones we expect to provide the yield
    combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
    df_combat = df_sig[df_sig['event_type'].isin(combat_types)].copy()
    
    print(f"Total Matches tracked in Signals Log: {df_sig['match_id'].nunique()}")
    print(f"Total Combat Signals Fired:          {len(df_combat)}")
    print(f"Avg Signals per Match:               {len(df_combat)/df_sig['match_id'].nunique():.2f}")

    # 3. Why were they skipped? (Pareto of Rejection)
    # This identifies the "Yield Killer"
    skips = df_combat[df_combat['decision'] == 'skip']
    print("\n--- Why we aren't trading (Skip Pareto) ---")
    print(skips['skip_reason'].value_counts().head(10))

    # 4. Impact of the "Next-Gen" Relaxed Funnel
    # (How many signals exist if we remove ALMOST everything?)
    # Rules: 15s age, 90c fill, NO edge check, NO momentum check
    raw_potential = df_combat[
        (pd.to_numeric(df_combat['steam_age_ms'], errors='coerce') <= 15000) &
        (pd.to_numeric(df_combat['ask'], errors='coerce') <= 0.90)
    ]
    
    print("\n--- Yield Sensitivity Analysis ---")
    print(f"Current Trade Yield (Strict):    33 signals")
    print(f"Potential Yield (Relaxed):       {len(raw_potential)} signals")
    print(f"Theoretical Max (No Filters):    {len(df_combat)} signals")

run_audit()
