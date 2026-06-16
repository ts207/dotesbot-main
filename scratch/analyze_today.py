import pandas as pd
import numpy as np
from datetime import datetime

def analyze():
    today = "2026-05-28"
    try:
        print(f"=== ANALYSIS FOR {today} ===")
        
        # 1. Signals Analysis
        df_sig = pd.read_csv('logs/signals.csv')
        df_sig = df_sig[df_sig['timestamp_utc'].str.startswith(today)]
        
        if df_sig.empty:
            print("No signals found for today.")
        else:
            print(f"\nTotal Signals Evaluated: {len(df_sig)}")
            print("\nDecisions:")
            print(df_sig['decision'].value_counts())
            
            print("\nTop Skip Reasons:")
            print(df_sig[df_sig['decision'] == 'skip']['skip_reason'].value_counts().head(5))
            
            print("\nEvent Types:")
            print(df_sig['event_type'].value_counts())

        # 2. Markouts Analysis
        try:
            df_mark = pd.read_csv('logs/signal_markouts.csv')
            df_mark = df_mark[df_mark['timestamp_utc'].str.startswith(today)]
            if not df_mark.empty:
                print(f"\nMarkout Stats (n={len(df_mark)}):")
                print(df_mark[['markout_3s', 'markout_10s', 'markout_30s']].mean())
                print(f"Win Rate (30s): {(df_mark['markout_30s'] > 0).mean():.1%}")
        except: pass

        # 3. Scalp Analysis
        for name, path in [("Dota Scalp", "logs/scalp_trades.csv"), ("LoL Scalp", "logs/lol_scalp_paper.csv")]:
            try:
                df = pd.read_csv(path)
                # Check for date in appropriate column
                date_col = 'closed_at_utc' if 'closed_at_utc' in df.columns else 'timestamp_utc'
                df_today = df[df[date_col].str.startswith(today)]
                print(f"\n{name} Today: n={len(df_today)}")
                if not df_today.empty:
                    pnl_col = 'pnl_usd' if 'pnl_usd' in df_today.columns else 'total_pnl_usd'
                    print(f"  Total PnL: ${df_today[pnl_col].sum():+.2f}")
            except: pass

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    analyze()
