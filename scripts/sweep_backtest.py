#!/usr/bin/env python3
import os
import subprocess
import pandas as pd
import json
import numpy as np

def run_sweep():
    edges = [0.005, 0.010, 0.015, 0.020, 0.025, 0.030, 0.040]
    results = []
    
    data_file = "data_v2/model_value_replay.parquet"
        
    for edge in edges:
        print(f"Running sweep for edge threshold: {edge}")
        env = os.environ.copy()
        env["MODEL_VALUE_MIN_EDGE"] = str(edge)
        env["MODEL_VALUE_CONFIRM_MIN_EDGE"] = str(edge)
        env["MODEL_VALUE_REQUIRE_NET_WORTH"] = "true"
        env["PYTHONPATH"] = "."
        
        # Out dir
        out_dir = f"reports/model_value_v1_edge_{edge}"
        os.makedirs(out_dir, exist_ok=True)
        
        cmd = [
            ".venv/bin/python3", "scripts/backtest_model_value_edge_v1.py",
            "--replay-file", data_file,
            "--out-dir", out_dir
        ]
        
        subprocess.run(cmd, env=env, check=True)
        
        # Analyze results
        signals_file = os.path.join(out_dir, "model_value_v1_signals.csv")
        trades_file = os.path.join(out_dir, "model_value_v1_trades.csv")
        
        num_signals = 0
        if os.path.exists(signals_file):
            sig_df = pd.read_csv(signals_file)
            num_signals = len(sig_df)
            
        if os.path.exists(trades_file) and os.path.getsize(trades_file) > 10:
            tr_df = pd.read_csv(trades_file)
            num_trades = len(tr_df)
            wins = len(tr_df[tr_df['settlement_outcome'] == 'WIN']) if 'settlement_outcome' in tr_df.columns else 0
            win_rate = wins / num_trades if num_trades > 0 else 0.0
            avg_ask = tr_df['entry_ask'].mean() if 'entry_ask' in tr_df.columns else np.nan
            roi = tr_df['roi'].mean() if 'roi' in tr_df.columns else np.nan
            clv_900s = tr_df['clv_900s'].mean() if 'clv_900s' in tr_df.columns else np.nan
            clv_1200s = tr_df['clv_1200s'].mean() if 'clv_1200s' in tr_df.columns else np.nan
        else:
            num_trades = 0
            win_rate = 0.0
            avg_ask = float('nan')
            roi = float('nan')
            clv_900s = float('nan')
            clv_1200s = float('nan')
            
        results.append({
            "edge_threshold": edge,
            "signals": num_signals,
            "confirmed_trades": num_trades,
            "win_rate": win_rate,
            "avg_ask": avg_ask,
            "roi": roi,
            "clv_900s": clv_900s,
            "clv_1200s": clv_1200s
        })
        
    print("\n--- Sweep Results ---")
    print("| edge threshold | signals | confirmed trades | win rate | avg ask | ROI | CLV 900s | CLV 1200s |")
    print("| -------------: | ------: | ---------------: | -------: | ------: | --: | -------: | --------: |")
    for r in results:
        print(f"| {r['edge_threshold']:.3f} | {r['signals']} | {r['confirmed_trades']} | {r['win_rate']:.1%} | {r['avg_ask']:.3f} | {r['roi']:.1%} | {r['clv_900s']:.1%} | {r['clv_1200s']:.1%} |")
        
    # Write to markdown
    with open("reports/model_value_v1_sweep_summary.md", "w") as f:
        f.write("# Model Value v1 Edge Threshold Sweep\n\n")
        f.write("| edge threshold | signals | confirmed trades | win rate | avg ask | ROI | CLV 900s | CLV 1200s |\n")
        f.write("| -------------: | ------: | ---------------: | -------: | ------: | --: | -------: | --------: |\n")
        for r in results:
            f.write(f"| {r['edge_threshold']:.3f} | {r['signals']} | {r['confirmed_trades']} | {r['win_rate']:.1%} | {r['avg_ask']:.3f} | {r['roi']:.1%} | {r['clv_900s']:.1%} | {r['clv_1200s']:.1%} |\n")
            
if __name__ == "__main__":
    run_sweep()
