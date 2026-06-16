import pandas as pd
import glob
import numpy as np

def run_audit():
    # 1. Get correct columns from signals.csv
    try:
        header_df = pd.read_csv('logs/signals.csv', nrows=0)
        cols = header_df.columns.tolist()
    except Exception as e:
        print(f"Error reading header: {e}")
        return

    # 2. Load and Aggregate ALL signal logs
    sig_files = glob.glob('logs/signals.csv*')
    all_sigs = []
    for f in sig_files:
        try:
            # Bak files might be headerless or have different headers
            # Let's read first few lines to check
            test_df = pd.read_csv(f, nrows=1)
            if 'timestamp_utc' in test_df.columns:
                df = pd.read_csv(f, on_bad_lines='skip')
            else:
                df = pd.read_csv(f, names=cols, on_bad_lines='skip')
            
            # Ensure timestamp_utc and match_id are strings for merging
            df['timestamp_utc'] = df['timestamp_utc'].astype(str)
            df['match_id'] = df['match_id'].astype(str)
            all_sigs.append(df)
            print(f"Loaded {f}: {len(df)} rows")
        except Exception as e:
            print(f"Error loading {f}: {e}")
    
    if not all_sigs:
        print("No signal logs loaded.")
        return
        
    df_sig = pd.concat(all_sigs, ignore_index=True)
    df_sig = df_sig.drop_duplicates(subset=['timestamp_utc', 'match_id'])
    print(f"Total Unique Signals: {len(df_sig)}")

    # 3. Load Markouts
    try:
        df_mark = pd.read_csv('logs/signal_markouts.csv')
        df_mark['signal_timestamp_utc'] = df_mark['signal_timestamp_utc'].astype(str)
        df_mark['match_id'] = df_mark['match_id'].astype(str)
        df_mark = df_mark.drop_duplicates(subset=['signal_timestamp_utc', 'match_id'])
        print(f"Total Unique Markouts: {len(df_mark)}")
    except Exception as e:
        print(f"Error loading markouts: {e}")
        return
    
    # 4. Merge
    merged = pd.merge(df_sig, df_mark[['signal_timestamp_utc', 'match_id', 'markout_30s', 'markout_10s', 'markout_3s']], 
                     left_on=['timestamp_utc', 'match_id'], 
                     right_on=['signal_timestamp_utc', 'match_id'], 
                     how='inner')
    
    if merged.empty:
        print("Join failed. Check timestamp formats.")
        # Print samples
        print("Signal TS Sample:", df_sig['timestamp_utc'].iloc[0])
        print("Markout TS Sample:", df_mark['signal_timestamp_utc'].iloc[0])
        return

    # 5. Filter for Combat Only
    combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
    merged = merged[merged['event_type'].isin(combat_types)].copy()
    
    # Numeric Coerce
    for col in ['steam_age_ms', 'ask', 'executable_edge', 'game_time_sec', 'event_quality']:
        merged[col] = pd.to_numeric(merged[col], errors='coerce')
    
    print(f"\n=== EXHAUSTIVE COMBAT SIGNAL AUDIT (n={len(merged)}) ===")
    
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
    winners = merged[merged['markout_30s'] >= 0.05]
    print(f"\nProfile of Big Winners (n={len(winners)}):")
    if not winners.empty:
        print(f"  Avg Age:     {winners['steam_age_ms'].mean():.0f}ms")
        print(f"  Avg Quality: {winners['event_quality'].mean():.2f}")
        print(f"  Avg Price:   {winners['ask'].mean():.2f}")
        print(f"  Avg Edge:    {winners['executable_edge'].mean():.4f}")

run_audit()
