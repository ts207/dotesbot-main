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
    print("Running base 0.02 test to get last_available_mid CLV...")
    tr_02, sig_02 = run_backtest("reports/robustness_base_0.02", {
        "MODEL_VALUE_MIN_EDGE": "0.02",
        "MODEL_VALUE_CONFIRM_MIN_EDGE": "0.02",
        "MODEL_VALUE_MAX_SPREAD": "0.05",
    })
    
    lines = ["# Robustness and Failure-Mode Tests", ""]
    
    # 2. Resolve-settlement sensitivity
    lines.append("## 1. Resolve-settlement sensitivity")
    total_trades = len(tr_02)
    resolved_trades = tr_02[tr_02['settlement_outcome'].isin(['WIN', 'LOSS'])]
    unresolved_trades = tr_02[~tr_02['settlement_outcome'].isin(['WIN', 'LOSS'])]
    
    stake = 5.0
    resolved_pnl = resolved_trades['pnl_usd'].sum() if not resolved_trades.empty else 0
    resolved_roi = resolved_pnl / (len(resolved_trades)*stake) if len(resolved_trades) > 0 else 0
    
    mtm_pnl = 0
    for _, row in unresolved_trades.iterrows():
        last_mid = row.get('last_available_mid', np.nan)
        if pd.notna(last_mid):
            mtm_pnl += (row['shares'] * last_mid) - stake
        else:
            mtm_pnl += 0
    
    mark_to_mid_roi = (resolved_pnl + mtm_pnl) / (total_trades * stake) if total_trades > 0 else 0
    
    worst_case_pnl = resolved_pnl + (-stake * len(unresolved_trades))
    worst_case_roi = worst_case_pnl / (total_trades * stake) if total_trades > 0 else 0
    
    lines.extend([
        f"67 total trades -> actually {total_trades}",
        f"{len(resolved_trades)} resolved trades",
        f"{len(unresolved_trades)} unresolved trades",
        f"resolved ROI: {resolved_roi:.2%}",
        f"mark-to-last-mid ROI: {mark_to_mid_roi:.2%}",
        f"worst-case ROI: {worst_case_roi:.2%}",
        ""
    ])
    
    # 3. Threshold sweep around 0.05-0.25
    lines.append("## 2. Threshold sweep around the actual passing edge range")
    lines.append("| threshold | signals | armed | confirmed | trades | resolved_trades | resolved_roi | mark_to_mid_roi | avg_edge | min_edge | p25_edge | p50_edge | p75_edge | avg_ask |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    
    thresholds = [0.02, 0.05, 0.075, 0.10, 0.125, 0.15, 0.16, 0.175, 0.20, 0.25]
    for th in thresholds:
        print(f"Sweeping threshold: {th}")
        tr, sig = run_backtest(f"reports/robustness_th_{th}", {
            "MODEL_VALUE_MIN_EDGE": th,
            "MODEL_VALUE_CONFIRM_MIN_EDGE": th,
            "MODEL_VALUE_MAX_SPREAD": "0.05",
        })
        signals = len(sig) if not sig.empty else 0
        armed = len(sig[sig['confirmation_reason'] == 'model_value_confirm_armed']) if not sig.empty else 0
        confirmed = len(sig[sig['confirmed'] == True]) if not sig.empty else 0
        trades = len(tr) if not tr.empty else 0
        
        if not tr.empty:
            res_tr = tr[tr['settlement_outcome'].isin(['WIN', 'LOSS'])]
            res_pnl = res_tr['pnl_usd'].sum()
            res_roi = res_pnl / (len(res_tr)*stake) if len(res_tr) > 0 else 0
            
            mtm_p = res_pnl
            for _, r in tr[~tr['settlement_outcome'].isin(['WIN', 'LOSS'])].iterrows():
                lmid = r.get('last_available_mid', np.nan)
                if pd.notna(lmid): mtm_p += (r['shares'] * lmid) - stake
            mtm_roi = (mtm_p + res_pnl) / (trades*stake) if trades > 0 else 0
            
            lines.append(f"| {th} | {signals} | {armed} | {confirmed} | {trades} | {len(res_tr)} | {res_roi:.2%} | {mtm_roi:.2%} | {tr['edge'].mean():.4f} | {tr['edge'].min():.4f} | {tr['edge'].quantile(0.25):.4f} | {tr['edge'].median():.4f} | {tr['edge'].quantile(0.75):.4f} | {tr['entry_ask'].mean():.4f} |")
        else:
            lines.append(f"| {th} | {signals} | {armed} | {confirmed} | 0 | 0 | 0% | 0% | - | - | - | - | - | - |")
    lines.append("")
    
    # 4. Spread sensitivity
    lines.append("## 3. Spread sensitivity")
    lines.append("| spread | trades | resolved trades | ROI | CLV 1200s | avg edge | rejected_spread_too_large |")
    lines.append("|---|---|---|---|---|---|---|")
    spreads = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08]
    for sp in spreads:
        print(f"Sweeping spread: {sp}")
        tr, sig = run_backtest(f"reports/robustness_sp_{sp}", {
            "MODEL_VALUE_MIN_EDGE": "0.02",
            "MODEL_VALUE_CONFIRM_MIN_EDGE": "0.02",
            "MODEL_VALUE_MAX_SPREAD": sp,
        })
        rej_spread = len(sig[sig['reject_reason'] == 'spread_too_large']) if not sig.empty else 0
        if not tr.empty:
            res_tr = tr[tr['settlement_outcome'].isin(['WIN', 'LOSS'])]
            res_pnl = res_tr['pnl_usd'].sum()
            res_roi = res_pnl / (len(res_tr)*stake) if len(res_tr) > 0 else 0
            clv = tr['clv_1200s'].mean()
            lines.append(f"| {sp} | {len(tr)} | {len(res_tr)} | {res_roi:.2%} | {clv:.4f} | {tr['edge'].mean():.4f} | {rej_spread} |")
        else:
            lines.append(f"| {sp} | 0 | 0 | 0% | 0 | N/A | {rej_spread} |")
    lines.append("")
    
    # Write report
    with open("reports/robustness_report.md", "w") as f:
        f.write("\n".join(lines))
    print("Report partially written to reports/robustness_report.md")

if __name__ == "__main__":
    main()
