import pandas as pd
import numpy as np

def analyze():
    match_id = "8829095250"
    try:
        # 1. Load Signals
        df_sig = pd.read_csv('logs/signals.csv')
        match_sigs = df_sig[df_sig['match_id'].astype(str) == match_id].copy()
        
        print(f"=== ANALYSIS: Team Spirit vs Team Liquid (ID: {match_id}) ===")
        print(f"Total Signals: {len(match_sigs)}")
        
        # 2. Decision Summary
        print("\nDecision Breakdown:")
        print(match_sigs['decision'].value_counts())
        
        # 3. Skip Pareto
        print("\nSkip Reasons:")
        print(match_sigs[match_sigs['decision'] == 'skip']['skip_reason'].value_counts())

        # 4. Critical Event Timeline
        match_sigs['game_time_min'] = match_sigs['game_time_sec'] / 60.0
        print("\nSignificant Events & Market Response:")
        cols = ['game_time_min', 'event_type', 'decision', 'skip_reason', 'ask', 'executable_edge', 'steam_age_ms']
        print(match_sigs[cols].sort_values('game_time_min').to_string(index=False))

    except Exception as e:
        print(f"Error: {e}")

analyze()
