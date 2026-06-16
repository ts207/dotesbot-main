#!/usr/bin/env python3
import os
import pandas as pd
import numpy as np
from datetime import datetime

DSWING_QUALITY_CSV = "logs/dswing_exit_quality.csv"
DSWING_ATTEMPTS_CSV = "logs/dswing_attempts.csv"

def load_csv(path):
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception as e:
        print(f"Error loading {path}: {e}")
        return pd.DataFrame()

def report():
    df = load_csv(DSWING_QUALITY_CSV)
    att = load_csv(DSWING_ATTEMPTS_CSV)

    if df.empty:
        print(f"No data found in {DSWING_QUALITY_CSV}")
        return

    print("=== DSWING EXIT QUALITY REPORT ===")
    print(f"Total exit rows: {len(df)}")
    
    # Filter to unique positions if there are multiple attempts per position
    # But usually we want to see the final outcome or all attempts.
    # Let's focus on unique positions for the summary.
    pos_summary = df.sort_values("exit_decision_ns").groupby("position_id").last().reset_index()
    
    print(f"Unique positions exited: {len(pos_summary)}")
    print("")

    metrics = {
        "Avg Entry Ask": pos_summary["entry_ask"].mean(),
        "Avg Entry Fair": pos_summary["entry_series_fair"].mean(),
        "Avg Entry Edge": pos_summary["entry_edge"].mean(),
        "Avg P_Game": pos_summary["entry_p_game"].mean(),
        "Avg Hold Sec": pos_summary["hold_sec"].mean(),
        "Avg Exit Delay Sec": pos_summary[pos_summary["exit_delay_sec"].notnull()]["exit_delay_sec"].mean(),
        "Avg Convergence Markout": pos_summary["convergence_markout"].mean(),
        "Avg Captured Edge": pos_summary["captured_edge"].mean(),
    }

    for k, v in metrics.items():
        if pd.notnull(v):
            print(f"{k:<25}: {v:>8.4f}")
        else:
            print(f"{k:<25}: {'N/A':>8}")

    print("\n--- Exit Reason Counts ---")
    print(pos_summary["exit_reason"].value_counts().to_string())

    print("\n--- Exit Order Status Counts ---")
    print(pos_summary["exit_order_status"].value_counts().to_string())

    if "entry_current_game_number" in pos_summary.columns:
        print("\n--- By Game Number ---")
        game_stats = pos_summary.groupby("entry_current_game_number")["captured_edge"].agg(["count", "mean"])
        print(game_stats.to_string())

    if "execution_path" in pos_summary.columns:
        print("\n--- By Execution Path ---")
        path_stats = pos_summary.groupby("execution_path")["captured_edge"].agg(["count", "mean"])
        print(path_stats.to_string())

    # Warning conditions
    print("\n--- Warnings ---")
    
    # 1. Missing map_end_convergence exits
    map_end_exits = pos_summary[pos_summary["exit_reason"] == "map_end_convergence"]
    if map_end_exits.empty:
        print("[!] WARNING: No 'map_end_convergence' exits found. Is the exit logic working?")
    
    # 2. High exit delay
    avg_delay = pos_summary["exit_delay_sec"].mean()
    if pd.notnull(avg_delay) and avg_delay > 30:
        print(f"[!] WARNING: Avg exit delay is high ({avg_delay:.1f}s). DSWING may be missing the window.")

    # 3. Negative captured edge
    avg_cap = pos_summary["captured_edge"].mean()
    if pd.notnull(avg_cap) and avg_cap <= 0:
        print(f"[!] WARNING: Avg captured edge is non-positive ({avg_cap:.4f}).")

    # 4. Failed exits
    failed = pos_summary[pos_summary["exit_order_status"].isin(["missing_book", "rejected", "rejected_balance"])]
    if not failed.empty:
        print(f"[!] WARNING: {len(failed)} positions failed to exit properly (status: {failed['exit_order_status'].unique()})")

if __name__ == "__main__":
    report()
