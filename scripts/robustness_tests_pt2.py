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
        "--out-dir", out_dir
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
    
    # 5. Confirmation sensitivity
    lines.append("## 4. Confirmation sensitivity")
    lines.append("| age_sec | ask_worsen | armed | confirmed | conversion_rate | trades | res_roi | clv_1200s |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for age in [30, 60, 90, 120]:
        for ask_w in [0.00, 0.01, 0.02, 0.03]:
            print(f"Sweeping confirm age={age}, ask_worsen={ask_w}")
            tr, sig = run_backtest(f"reports/robustness_conf_{age}_{ask_w}", {
                "MODEL_VALUE_MIN_EDGE": "0.02",
                "MODEL_VALUE_CONFIRM_MIN_EDGE": "0.02",
                "MODEL_VALUE_CONFIRM_MAX_AGE_SEC": age,
                "MODEL_VALUE_CONFIRM_MAX_ASK_WORSEN": ask_w,
            })
            if not sig.empty:
                armed = len(sig[sig['confirmation_reason'] == 'model_value_confirm_armed'])
                confirmed = len(sig[sig['confirmed'] == True])
                conv_rate = confirmed / armed if armed > 0 else 0
            else:
                armed, confirmed, conv_rate = 0, 0, 0
                
            if not tr.empty:
                res_tr = tr[tr['settlement_outcome'].isin(['WIN', 'LOSS'])]
                res_pnl = res_tr['pnl_usd'].sum()
                res_roi = res_pnl / (len(res_tr)*stake) if len(res_tr) > 0 else 0
                clv = tr['clv_1200s'].mean()
                lines.append(f"| {age} | {ask_w} | {armed} | {confirmed} | {conv_rate:.2%} | {len(tr)} | {res_roi:.2%} | {clv:.4f} |")
            else:
                lines.append(f"| {age} | {ask_w} | {armed} | {confirmed} | {conv_rate:.2%} | 0 | 0% | 0 |")
    lines.append("")
    
    # 6. Book-age sensitivity
    lines.append("## 5. Book-age sensitivity")
    lines.append("| max_book_age_ms | avg_book_age | p90_book_age | trades | res_roi | clv_1200s |")
    lines.append("|---|---|---|---|---|---|")
    for bage in [1000, 2500, 5000, 10000, 15000, 30000]:
        print(f"Sweeping book_age={bage}")
        tr, sig = run_backtest(f"reports/robustness_bage_{bage}", {
            "MODEL_VALUE_MIN_EDGE": "0.02",
            "MODEL_VALUE_CONFIRM_MIN_EDGE": "0.02",
            "MAX_BOOK_AGE_MS": bage,
        })
        if not tr.empty:
            if 'book_age_ms' not in tr.columns:
                sig_merge = sig[['timestamp_ns', 'token_id', 'book_age_ms']].copy()
                sig_merge.rename(columns={'timestamp_ns': 'entry_timestamp_ns'}, inplace=True)
                tr = tr.merge(sig_merge, on=['entry_timestamp_ns', 'token_id'], how='left')
            avg_b = tr['book_age_ms'].mean()
            p90_b = tr['book_age_ms'].quantile(0.9)
            res_tr = tr[tr['settlement_outcome'].isin(['WIN', 'LOSS'])]
            res_pnl = res_tr['pnl_usd'].sum()
            res_roi = res_pnl / (len(res_tr)*stake) if len(res_tr) > 0 else 0
            clv = tr['clv_1200s'].mean()
            lines.append(f"| {bage} | {avg_b:.1f} | {p90_b:.1f} | {len(tr)} | {res_roi:.2%} | {clv:.4f} |")
        else:
            lines.append(f"| {bage} | - | - | 0 | 0% | 0 |")
    lines.append("")
    
    with open("reports/robustness_report.md", "a") as f:
        f.write("\n".join(lines))
    print("Report pt2 appended.")

if __name__ == "__main__":
    main()
