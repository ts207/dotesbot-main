import pandas as pd
import glob
import numpy as np

def analyze():
    sig_files = ['logs/signals.csv'] + glob.glob('logs/signals.csv*.bak')
    all_df = []
    cols = pd.read_csv('logs/signals.csv', nrows=0).columns
    for f in sig_files:
        try:
            if 'bak' in f:
                df = pd.read_csv(f, names=cols, on_bad_lines='skip')
            else:
                df = pd.read_csv(f, on_bad_lines='skip')
            all_df.append(df)
        except: pass
    
    df = pd.concat(all_df, ignore_index=True)
    
    # 1. Filter for the 2 combat events only
    combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
    df_combat = df[df['event_type'].isin(combat_types)].copy()
    
    print(f"=== FUNNEL ANALYSIS: {len(df_combat)} Total Combat Signal Fires ===")
    
    # 2. Analyze the 'decision' distribution
    print("\nDecision Distribution:")
    print(df_combat['decision'].value_counts())

    # 3. Analyze skip reasons specifically for combat events
    print("\nSkip Reasons for Combat Events:")
    skips = df_combat[df_combat['decision'] == 'skip']
    print(skips['skip_reason'].value_counts())

    # 4. Filter impact of our current "Gold Standard" logic
    # (How many would still be skipped under current 15s + 90c rules?)
    df_combat['steam_age_ms'] = pd.to_numeric(df_combat['steam_age_ms'], errors='coerce')
    df_combat['ask'] = pd.to_numeric(df_combat['ask'], errors='coerce')
    
    print("\nRemaining Skips under 15s + 90c Logic:")
    # If it was skipped for stale, but age <= 15000, it's now a "SAVE"
    saved = skips[(skips['skip_reason'] == 'steam_stale') & (skips['steam_age_ms'] <= 15000)]
    print(f"Signals 'Saved' from Staleness by 15s Gate: {len(saved)}")
    
    # What are the actual hard rejections left?
    hard_rejects = skips[
        ~((skips['skip_reason'] == 'steam_stale') & (skips['steam_age_ms'] <= 15000)) &
        ~((skips['skip_reason'] == 'fill_price_too_high') & (skips['ask'] <= 0.90))
    ]
    print("\nHard Rejection Pareto (Combat Only):")
    print(hard_rejects['skip_reason'].value_counts())

if __name__ == "__main__":
    analyze()
