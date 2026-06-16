import pandas as pd
import numpy as np

def analyze():
    today_date = '2026-05-28'
    
    # 1. Load Signals
    try:
        df_sig = pd.read_csv('logs/signals.csv')
        df_sig['timestamp_utc'] = pd.to_datetime(df_sig['timestamp_utc'])
        df_today_sig = df_sig[df_sig['timestamp_utc'].dt.date == pd.Timestamp(today_date).date()].copy()
    except Exception as e:
        print(f"Error loading signals: {e}")
        return

    # 2. Load Dota Events
    try:
        df_ev = pd.read_csv('logs/dota_events.csv')
        # dota_events.csv doesn't have a direct timestamp in some formats, let's check
        # Usually it has 'match_id', 'event_type', 'game_time_sec'
        # We'll filter events for match IDs seen today
        today_match_ids = df_today_sig['match_id'].unique()
        df_today_ev = df_ev[df_ev['match_id'].isin(today_match_ids)].copy()
    except Exception as e:
        print(f"Error loading dota events: {e}")
        df_today_ev = pd.DataFrame()

    # 3. Load Markouts
    try:
        df_mark = pd.read_csv('logs/signal_markouts.csv')
        df_mark['timestamp_utc'] = pd.to_datetime(df_mark['timestamp_utc'])
        df_today_mark = df_mark[df_mark['timestamp_utc'].dt.date == pd.Timestamp(today_date).date()].copy()
    except Exception as e:
        print(f"Error loading markouts: {e}")
        df_today_mark = pd.DataFrame()

    print(f"=== EVENT ANALYSIS FOR {today_date} ===")
    print(f"Total Matches tracked with signals: {len(today_match_ids)}")
    print(f"Total Events detected: {len(df_today_ev)}")
    print(f"Total Signals evaluated: {len(df_today_sig)}")
    print(f"Total Markouts recorded: {len(df_today_mark)}")

    # 4. Event Type Distribution
    print("\n--- Event Distribution (Signals) ---")
    ev_counts = df_today_sig['event_type'].value_counts()
    print(ev_counts)

    # 5. Signal Performance by Event Type
    if not df_today_mark.empty:
        print("\n--- Mean Markout (30s) by Event Type ---")
        # Join markouts with signals to get event types if not present
        # Actually signal_markouts.csv has event_type
        type_perf = df_today_mark.groupby('event_type')['markout_30s'].agg(['count', 'mean', 'median'])
        print(type_perf.sort_values('mean', ascending=False))

    # 6. Skip Reason Pareto
    print("\n--- Top Skip Reasons ---")
    skips = df_today_sig[df_today_sig['decision'] == 'skip']
    print(skips['skip_reason'].value_counts().head(10))

    # 7. Analysis of the "Unmapped" matches
    unmapped = df_today_sig[df_today_sig['market_name'].isna()]
    if not unmapped.empty:
        print(f"\n--- Unmapped Match IDs (Total signals: {len(unmapped)}) ---")
        print(unmapped['match_id'].value_counts().head(5))

    # 8. High Value Signals (Markout > 0.02 at 30s)
    if not df_today_mark.empty:
        high_val = df_today_mark[df_today_mark['markout_30s'] >= 0.02]
        print(f"\n--- High Alpha Signals (M30 >= 0.02, n={len(high_val)}) ---")
        if not high_val.empty:
            print(high_val[['event_type', 'markout_30s', 'match_id']].sort_values('markout_30s', ascending=False).head(10).to_string(index=False))

if __name__ == "__main__":
    analyze()
