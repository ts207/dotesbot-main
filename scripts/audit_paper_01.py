import pandas as pd
import numpy as np
import os
import time

def audit_paper_run():
    print(f"=== Paper Run Audit @ {time.ctime()} ===")
    
    # 1. Check if real-live attempts were made (should be empty/non-existent or not growing)
    live_path = "logs/live_attempts.csv"
    if os.path.exists(live_path):
        # Could just check if new live attempts are being made
        pass
    else:
        print("PASS: No live_attempts.csv found.")

    paper_path = "logs/paper_trades.csv"
    if not os.path.exists(paper_path):
        print("No paper trades yet.")
        return False
        
    try:
        df = pd.read_csv(paper_path)
    except pd.errors.EmptyDataError:
        print("No paper trades yet (file empty).")
        return False

    # Filter for our run_id or just all recent ones if no run_id is explicit.
    # Actually, we can just look at the whole file if we assume it was cleared, or look at timestamps.
    print(f"Total paper trades logged: {len(df)}")
    
    if len(df) == 0:
        return False

    # Track metrics
    settled = df[df['settlement_outcome'].isin(['WIN', 'LOSS'])]
    print(f"Settled trades: {len(settled)}")
    
    if len(settled) > 0:
        wins = len(settled[settled['settlement_outcome'] == 'WIN'])
        win_rate = wins / len(settled)
        print(f"Win rate: {win_rate:.2%}")
        
        stake = 5.0  # assumed
        # If shares are populated properly, we can calculate real PnL, otherwise use expected.
        total_pnl = settled['pnl_usd'].sum() if 'pnl_usd' in settled.columns else 0.0
        roi = total_pnl / (len(settled) * stake) if total_pnl != 0 else 0.0
        print(f"Settlement ROI: {roi:.2%}")
        
        # Profit concentration
        if 'pnl_usd' in settled.columns:
            sorted_pnl = settled['pnl_usd'].sort_values(ascending=False)
            top1 = sorted_pnl.iloc[0]
            top3 = sorted_pnl.iloc[:3].sum() if len(sorted_pnl) >= 3 else total_pnl
            
            roi_excl_1 = (total_pnl - top1) / ((len(settled) - 1) * stake) if len(settled) > 1 else 0
            roi_excl_3 = (total_pnl - top3) / ((len(settled) - 3) * stake) if len(settled) > 3 else 0
            
            print(f"ROI excluding best 1: {roi_excl_1:.2%}")
            print(f"ROI excluding best 3: {roi_excl_3:.2%}")
            
    # Check CLV
    for clv_col in ['clv_30s', 'clv_120s', 'clv_300s', 'clv_900s', 'clv_1200s']:
        if clv_col in df.columns:
            print(f"Avg {clv_col}: {df[clv_col].mean():.4f}")

    if 'entry_ask' in df.columns:
        print(f"Avg entry ask: {df['entry_ask'].mean():.4f}")
    if 'edge' in df.columns:
        print(f"Avg edge: {df['edge'].mean():.4f}")
    if 'book_age_ms' in df.columns:
        print(f"Avg book age: {df['book_age_ms'].mean():.1f} ms")
        if df['book_age_ms'].max() > 15000:
            print("WARNING: Book age > 15000ms found!")
            
    if 'spread' in df.columns:
        print(f"Avg spread: {df['spread'].mean():.4f}")

    if 'game_time_sec' in df.columns:
        # Time bucket
        def get_bucket(s):
            if pd.isna(s): return "Unknown"
            m = s / 60
            if m < 10: return "0-10m"
            elif m < 20: return "10-20m"
            elif m < 30: return "20-30m"
            elif m < 40: return "30-40m"
            else: return "40m+"
            
        buckets = df['game_time_sec'].apply(get_bucket).value_counts()
        print("Entry game-time buckets:")
        for k, v in buckets.items():
            print(f"  {k}: {v}")
            if k == "40m+":
                print("FAIL: 40m+ entries detected!")
                
    # Model sanity
    if 'model_version' in df.columns:
        print("Model versions logged:")
        print(df['model_version'].value_counts())
    else:
        print("WARNING: model_version not logged!")
        
    if 'confirmation_reason' in df.columns:
        print("Confirmation reasons logged:")
        print(df['confirmation_reason'].value_counts())
        
    return len(df) >= 50

if __name__ == "__main__":
    audit_paper_run()
