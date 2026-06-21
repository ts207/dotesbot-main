import pandas as pd
import os
import json

def main():
    print("--- LIVE PAPER LOG AUDIT ---")
    
    # 1. strategy_signals.csv
    try:
        sig_df = pd.read_csv("logs/strategy_signals.csv", on_bad_lines="skip")
        print("Unique strategies in signals:", sig_df['strategy'].unique() if 'strategy' in sig_df.columns else "No strategy col")
        model_sigs = sig_df[sig_df['strategy'] == 'MODEL_VALUE']
        print(f"Total model_value signals: {len(model_sigs)}")
        if not model_sigs.empty:
            missing_ver = model_sigs['model_version'].isna().sum()
            print(f"Signals missing model_version: {missing_ver}")
    except FileNotFoundError:
        print("logs/strategy_signals.csv not found")

    # 2. paper_attempts.csv
    try:
        atm_df = pd.read_csv("logs/paper_attempts.csv")
        print("Unique event_types in attempts:", atm_df['event_type'].unique() if 'event_type' in atm_df.columns else "No event_type col")
        model_atm = atm_df[atm_df['event_type'] == 'MODEL_VALUE']
        print(f"Total model_value paper entries: {len(model_atm)}")
        if not model_atm.empty:
            outside_time = model_atm[(model_atm['game_time_sec'] < 420) | (model_atm['game_time_sec'] > 2400)]
            print(f"Entries outside 420-2400s: {len(outside_time)}")
            above_spread = model_atm[model_atm['spread'] > 0.05]
            print(f"Entries above spread 0.05: {len(above_spread)}")
            above_age = model_atm[model_atm['book_age_ms'] > 5000]
            print(f"Entries above book_age 5000ms: {len(above_age)}")
            print(f"Average entry ask: {model_atm['ask'].mean():.4f}")
    except FileNotFoundError:
        print("logs/paper_attempts.csv not found")

    # 3. live_attempts.csv
    if os.path.exists("logs/live_attempts.csv"):
        live_df = pd.read_csv("logs/live_attempts.csv")
        print(f"Real live attempts: {len(live_df)}")
    else:
        print("Real live attempts: 0 (file not found)")

    # 4. paper_positions_v2.json
    try:
        with open("logs/paper_positions_v2.json") as f:
            positions = json.load(f)
        print(f"Open paper positions: {len(positions)}")
    except FileNotFoundError:
        print("logs/paper_positions_v2.json not found")

if __name__ == "__main__":
    main()
