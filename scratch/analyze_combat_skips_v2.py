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
    combat_types = ['POLL_FIGHT_SWING', 'POLL_LATE_FIGHT_FLIP']
    df_combat = df[df['event_type'].isin(combat_types)].copy()
    
    df_combat['steam_age_ms'] = pd.to_numeric(df_combat['steam_age_ms'], errors='coerce')
    df_combat['ask'] = pd.to_numeric(df_combat['ask'], errors='coerce')
    
    print(f"=== FUNNEL: {len(df_combat)} Total Combat Signal Fires ===")

    # Actual traded
    traded = df_combat[df_combat['decision'] == 'paper_buy_yes']
    print(f"Actually Traded (Old Logic): {len(traded)}")

    # Pareto of Skip Reasons
    skips = df_combat[df_combat['decision'] == 'skip']
    print("\nWhy they were skipped (Top 5):")
    print(skips['skip_reason'].value_counts().head(5))

    # Rejection breakdown
    print("\nHard Constraints Analysis:")
    
    # 1. Terminal/Price cap (The biggest killer)
    priced_out = skips[(skips['skip_reason'] == 'fill_price_too_high') & (skips['ask'] > 0.90)]
    print(f"- Priced out (>0.90): {len(priced_out)} signals")

    # 2. Market already reacted (Repricing speed)
    already_moved = skips[skips['skip_reason'].isin(['chasing_terminal_price', 'already_repriced', 'edge_too_small'])]
    print(f"- Market too fast (Already Repriced): {len(already_moved)} signals")

    # 3. Data Infrastructure (Staleness/Missing data)
    infra_skips = skips[skips['skip_reason'].isin(['steam_stale', 'book_stale', 'missing_book'])]
    print(f"- Data Pipeline Lag (>15s or missing book): {len(infra_skips)} signals")

if __name__ == "__main__":
    analyze()
