import pandas as pd
import glob
import numpy as np

def run_audit():
    # 1. Load and Aggregate ALL signal logs
    sig_files = ['logs/signals.csv'] + glob.glob('logs/signals.csv*.bak')
    all_sigs = []
    cols = pd.read_csv('logs/signals.csv', nrows=0).columns
    for f in sig_files:
        try:
            df = pd.read_csv(f, names=cols, on_bad_lines='skip') if 'bak' in f else pd.read_csv(f, on_bad_lines='skip')
            all_sigs.append(df)
        except: pass
    
    df_sig = pd.concat(all_sigs, ignore_index=True)
    
    # 2. Load Markouts
    df_mark = pd.read_csv('logs/signal_markouts.csv')
    df_mark = df_mark.drop_duplicates(subset=['signal_timestamp_utc', 'match_id'])
    
    # 3. Merge
    merged = pd.merge(df_sig, df_mark[['signal_timestamp_utc', 'match_id', 'markout_30s', 'markout_10s', 'markout_3s']], 
                     left_on=['timestamp_utc', 'match_id'], 
                     right_on=['signal_timestamp_utc', 'match_id'], 
                     how='inner')
    
    # 4. Filter for Combat Only
    combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
    merged = merged[merged['event_type'].isin(combat_types)].copy()
    
    # Numeric Coerce
    for col in ['steam_age_ms', 'ask', 'executable_edge', 'game_time_sec', 'event_quality']:
        merged[col] = pd.to_numeric(merged[col], errors='coerce')
    
    print(f"=== EXHAUSTIVE COMBAT SIGNAL AUDIT (n={len(merged)}) ===")
    
    # A. Yield & Quality
    print("\nYield by Event Type:")
    print(merged['event_type'].value_counts())
    
    # B. Markout Performance (Alpha)
    print("\nMean Alpha by Window:")
    print(merged[['markout_3s', 'markout_10s', 'markout_30s']].mean())
    
    # C. Alpha by Game Phase
    merged['phase'] = pd.cut(merged['game_time_sec'], bins=[0, 900, 1800, 2700, 10000], labels=['early', 'mid', 'late', 'ultra'])
    print("\nAlpha (M30) by Game Phase:")
    print(merged.groupby('phase', observed=False)['markout_30s'].mean())

    # D. Funnel Bottlenecks
    print("\nSkip Reason Pareto for Combat:")
    print(merged[merged['decision'] == 'skip']['skip_reason'].value_counts().head(10))

    # E. High-Alpha Signal Profiling
    # What do the "Big Winners" (>5c) look like?
    winners = merged[merged['markout_30s'] >= 0.05]
    print(f"\nProfile of Big Winners (n={len(winners)}):")
    if not winners.empty:
        print(f"  Avg Age:     {winners['steam_age_ms'].mean():.0f}ms")
        print(f"  Avg Quality: {winners['event_quality'].mean():.2f}")
        print(f"  Avg Price:   {winners['ask'].mean():.2f}")
        print(f"  Avg Edge:    {winners['executable_edge'].mean():.4f}")

run_audit()
