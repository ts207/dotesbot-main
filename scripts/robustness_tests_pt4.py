#!/usr/bin/env python3
import os
import pandas as pd
import numpy as np

def main():
    stake = 5.0
    lines = []
    
    tr_02 = pd.read_csv("reports/robustness_base_0.02/model_value_v1_trades.csv")
    sig_02 = pd.read_csv("reports/robustness_base_0.02/model_value_v1_signals.csv")
    
    # 8. Profit concentration
    lines.append("## 8. Profit concentration")
    res_tr = tr_02[tr_02['settlement_outcome'].isin(['WIN', 'LOSS'])].copy()
    if not res_tr.empty:
        res_tr = res_tr.sort_values('pnl_usd', ascending=False)
        top1 = res_tr.iloc[0]['pnl_usd']
        top3 = res_tr.iloc[:3]['pnl_usd'].sum() if len(res_tr) >= 3 else res_tr['pnl_usd'].sum()
        top5 = res_tr.iloc[:5]['pnl_usd'].sum() if len(res_tr) >= 5 else res_tr['pnl_usd'].sum()
        
        total_pnl = res_tr['pnl_usd'].sum()
        roi_excl_1 = (total_pnl - top1) / ((len(res_tr)-1)*stake) if len(res_tr) > 1 else 0
        roi_excl_3 = (total_pnl - top3) / ((len(res_tr)-3)*stake) if len(res_tr) > 3 else 0
        roi_excl_5 = (total_pnl - top5) / ((len(res_tr)-5)*stake) if len(res_tr) > 5 else 0
        
        lines.append(f"- top 1 trade contribution: ${top1:.2f} of ${total_pnl:.2f}")
        lines.append(f"- top 3 trade contribution: ${top3:.2f} of ${total_pnl:.2f}")
        lines.append(f"- top 5 trade contribution: ${top5:.2f} of ${total_pnl:.2f}")
        lines.append(f"- ROI excluding best 1: {roi_excl_1:.2%}")
        lines.append(f"- ROI excluding best 3: {roi_excl_3:.2%}")
        lines.append(f"- ROI excluding best 5: {roi_excl_5:.2%}")
    else:
        lines.append("No resolved trades.")
    lines.append("")

    # 9. CLV split
    lines.append("## 9. CLV split for resolved vs unresolved")
    lines.append("| split | count | 30s | 120s | 300s | 900s | 1200s | last_mid_clv |")
    lines.append("|---|---|---|---|---|---|---|---|")
    
    def clv_row(name, df):
        if df.empty: return f"| {name} | 0 | - | - | - | - | - | - |"
        c30 = df['clv_30s'].mean()
        c120 = df['clv_1200s'].mean() # Actually 120s
        c120 = df['clv_120s'].mean()
        c300 = df['clv_300s'].mean()
        c900 = df['clv_900s'].mean()
        c1200 = df['clv_1200s'].mean()
        

        
        last_mids_clv = []
        for _, r in df.iterrows():
            lm = r.get('last_available_mid', np.nan)
            if pd.notna(lm): last_mids_clv.append(lm - r['entry_ask'])
        lmc = np.nanmean(last_mids_clv) if last_mids_clv else np.nan
        
        return f"| {name} | {len(df)} | {c30:.4f} | {c120:.4f} | {c300:.4f} | {c900:.4f} | {c1200:.4f} | {lmc:.4f} |"

    res_df = tr_02[tr_02['settlement_outcome'].isin(['WIN', 'LOSS'])]
    unres_df = tr_02[~tr_02['settlement_outcome'].isin(['WIN', 'LOSS'])]
    lines.append(clv_row("all trades", tr_02))
    lines.append(clv_row("resolved", res_df))
    lines.append(clv_row("unresolved", unres_df))
    lines.append("")
    
    # 10. Feature regime diagnostics
    lines.append("## 10. Feature-regime diagnostics")
    if not res_df.empty:
        win_tr = res_df[res_df['settlement_outcome'] == 'WIN']
        los_tr = res_df[res_df['settlement_outcome'] == 'LOSS']
    else:
        win_tr, los_tr = pd.DataFrame(), pd.DataFrame()
        
    pos_clv = tr_02[tr_02['clv_1200s'] > 0]
    neg_clv = tr_02[tr_02['clv_1200s'] <= 0]
    
    lines.append("| metric | all trades | winners | losers | pos_clv | neg_clv |")
    lines.append("|---|---|---|---|---|---|")
    
    def m(df, col): return df[col].mean() if not df.empty and col in df.columns else np.nan
    
    for col in ['market_mid', 'entry_ask', 'game_time_sec', 'token_net_worth_lead', 'token_score_margin', 'model_probability', 'edge']:
        lines.append(f"| {col} | {m(tr_02, col):.4f} | {m(win_tr, col):.4f} | {m(los_tr, col):.4f} | {m(pos_clv, col):.4f} | {m(neg_clv, col):.4f} |")
    lines.append("")
    
    # 11. Model-output sanity
    lines.append("## 11. Model-output sanity (across all signals)")
    sig_prob = sig_02['model_probability'].dropna()
    sig_edge = sig_02['edge'].dropna()
    
    def quantiles(s):
        if len(s) == 0: return "N/A"
        return f"min={s.min():.4f} | p1={s.quantile(0.01):.4f} | p10={s.quantile(0.10):.4f} | p50={s.median():.4f} | p90={s.quantile(0.90):.4f} | p99={s.quantile(0.99):.4f} | max={s.max():.4f}"
        
    lines.append(f"- **predicted_residual**: (Derived from probability - mid) -> Probability metrics: {quantiles(sig_prob)}")
    lines.append(f"- **edge**: {quantiles(sig_edge)}")
    
    clipped_0 = len(sig_prob[sig_prob <= 0.001])
    clipped_1 = len(sig_prob[sig_prob >= 0.999])
    lines.append(f"- signals clipped to 0: {clipped_0}")
    lines.append(f"- signals clipped to 1: {clipped_1}")
    lines.append("")
    
    with open("reports/robustness_report.md", "a") as f:
        f.write("\n".join(lines))
    print("Report pt4 appended.")

if __name__ == "__main__":
    main()
