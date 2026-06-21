#!/usr/bin/env python3
import os
import subprocess
import pandas as pd
import numpy as np

def run_backtest(out_dir, env_updates):
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    for k, v in env_updates.items():
        env[k] = str(v)
    os.makedirs(out_dir, exist_ok=True)
    cmd = [
        "python3", "scripts/backtest_model_value_edge_v1.py",
        "--replay-file", "data_v2/model_value_replay.parquet",
        "--out-dir", out_dir,
        "--no-filter"
    ]
    subprocess.run(cmd, env=env, check=True)
    
    trades_path = os.path.join(out_dir, "model_value_v1_trades.csv")
    signals_path = os.path.join(out_dir, "model_value_v1_signals.csv")
    
    tr_df = pd.read_csv(trades_path) if os.path.exists(trades_path) and os.path.getsize(trades_path) > 10 else pd.DataFrame()
    sig_df = pd.read_csv(signals_path) if os.path.exists(signals_path) and os.path.getsize(signals_path) > 10 else pd.DataFrame()
    
    return tr_df, sig_df

def main():
    stake = 5.0
    lines = []
    
    # 6. Ask-band sensitivity
    lines.append("## 6. Ask-band sensitivity")
    lines.append("| band | trades | res_roi | clv_1200s |")
    lines.append("|---|---|---|---|")
    bands = [(0.05, 0.95), (0.10, 0.90), (0.15, 0.85), (0.20, 0.80), (0.25, 0.75)]
    for mn, mx in bands:
        print(f"Sweeping ask_band={mn}-{mx}")
        tr, _ = run_backtest(f"reports/robustness_ask_{mn}_{mx}", {
            "MODEL_VALUE_MIN_EDGE": "0.02",
            "MODEL_VALUE_CONFIRM_MIN_EDGE": "0.02",
            "MODEL_VALUE_MIN_ASK": mn,
            "MODEL_VALUE_MAX_ASK": mx,
        })
        if not tr.empty:
            res_tr = tr[tr['settlement_outcome'].isin(['WIN', 'LOSS'])]
            res_pnl = res_tr['pnl_usd'].sum()
            res_roi = res_pnl / (len(res_tr)*stake) if len(res_tr) > 0 else 0
            clv = tr['clv_1200s'].mean()
            lines.append(f"| {mn}-{mx} | {len(tr)} | {res_roi:.2%} | {clv:.4f} |")
        else:
            lines.append(f"| {mn}-{mx} | 0 | 0% | 0 |")
    lines.append("")

    # 7. Game-time segmentation
    lines.append("## 7. Game-time segmentation")
    lines.append("| segment | trades | res_roi | clv_1200s |")
    lines.append("|---|---|---|---|")
    segments = [
        ("0-10m", 0, 600),
        ("10-20m", 600, 1200),
        ("20-30m", 1200, 1800),
        ("30-40m", 1800, 2400),
        ("40m+", 2400, 999999),
    ]
    for name, mn, mx in segments:
        print(f"Sweeping game_time={name}")
        tr, _ = run_backtest(f"reports/robustness_time_{mn}_{mx}", {
            "MODEL_VALUE_MIN_EDGE": "0.02",
            "MODEL_VALUE_CONFIRM_MIN_EDGE": "0.02",
            "MODEL_VALUE_MIN_GAME_TIME_SEC": mn,
            "MODEL_VALUE_MAX_GAME_TIME_SEC": mx,
        })
        if not tr.empty:
            res_tr = tr[tr['settlement_outcome'].isin(['WIN', 'LOSS'])]
            res_pnl = res_tr['pnl_usd'].sum()
            res_roi = res_pnl / (len(res_tr)*stake) if len(res_tr) > 0 else 0
            clv = tr['clv_1200s'].mean()
            lines.append(f"| {name} | {len(tr)} | {res_roi:.2%} | {clv:.4f} |")
        else:
            lines.append(f"| {name} | 0 | 0% | 0 |")
    lines.append("")

    with open("reports/robustness_report.md", "a") as f:
        f.write("\n".join(lines))
    print("Report pt3 appended.")

if __name__ == "__main__":
    main()
