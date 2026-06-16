import pandas as pd
import glob
import numpy as np

def run_census():
    # 1. Load EVERY signal log (Current + Backups)
    sig_files = glob.glob('logs/signals.csv*')
    all_sigs = []
    # Use headers from the current file
    cols = pd.read_csv('logs/signals.csv', nrows=0).columns.tolist()
    
    for f in sig_files:
        try:
            if 'bak' in f:
                df = pd.read_csv(f, names=cols, on_bad_lines='skip')
            else:
                df = pd.read_csv(f, on_bad_lines='skip')
            all_sigs.append(df)
        except: pass
    
    full_df = pd.concat(all_sigs, ignore_index=True)
    full_df['ask'] = pd.to_numeric(full_df['ask'], errors='coerce')
    full_df['steam_age_ms'] = pd.to_numeric(full_df['steam_age_ms'], errors='coerce')
    
    # 2. Filter for Combat Only
    combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
    df_combat = full_df[full_df['event_type'].isin(combat_types)].copy()
    
    print(f"=== ABSOLUTE RAW YIELD CENSUS (n={len(df_combat)} Total Fires) ===")
    
    # 3. New Aggressive Funnel: 30s Age + 95c Cap + NO momentum checks
    # (By taking all rows that pass age/price, we effectively ignore momentum skips)
    aggressive_yield = df_combat[
        (df_combat['steam_age_ms'] <= 30000) &
        (df_combat['ask'] <= 0.95)
    ]
    
    print(f"Aggressive Yield (30s/95c/All): {len(aggressive_yield)} trades")
    print(f"Matches Covered:               {aggressive_yield['match_id'].nunique()}")

    # 4. Compare with the 33 from before (15s/90c)
    semi_strict = df_combat[
        (df_combat['steam_age_ms'] <= 15000) &
        (df_combat['ask'] <= 0.90)
    ]
    print(f"Semi-Strict Yield (15s/90c):    {len(semi_strict)} trades")

    # 5. Why the discrepancy?
    print("\n--- Skip Pareto for the Raw Combat Fires ---")
    print(df_combat['skip_reason'].value_counts().head(10))

run_census()
