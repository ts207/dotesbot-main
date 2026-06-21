import pandas as pd
import numpy as np
import os

def analyze_trades(th_dir, th):
    trades_path = os.path.join(th_dir, "model_value_v1_trades.csv")
    signals_path = os.path.join(th_dir, "model_value_v1_signals.csv")
    
    if not os.path.exists(trades_path) or not os.path.exists(signals_path):
        return [f"## Threshold {th}", "No data found.", ""]
        
    trades = pd.read_csv(trades_path)
    signals = pd.read_csv(signals_path)
    
    lines = [f"## Threshold {th}"]
    lines.append(f"- Trades: {len(trades)}")
    
    if len(trades) == 0:
        return lines + [""]
        
    # Settlement ROI
    res_trades = trades[trades['settlement_outcome'].isin(['WIN', 'LOSS'])]
    if len(res_trades) > 0:
        pnl = res_trades['pnl_usd'].sum()
        cost = len(res_trades) * 5.0
        roi = pnl / cost
        lines.append(f"- Settlement ROI: {roi:.2%}")
    else:
        lines.append("- Settlement ROI: N/A")
        
    # CLV
    clv_900 = trades['clv_900s'].mean() if 'clv_900s' in trades.columns else 0.0
    clv_1200 = trades['clv_1200s'].mean() if 'clv_1200s' in trades.columns else 0.0
    lines.append(f"- CLV_900s: {clv_900:.4f}")
    lines.append(f"- CLV_1200s: {clv_1200:.4f}")
    
    # Profit concentration
    if len(res_trades) > 3:
        profits = res_trades['pnl_usd'].sort_values(ascending=False).values
        roi_ex_3 = (sum(profits[3:])) / ((len(res_trades) - 3) * 5.0)
        lines.append(f"- ROI excluding best 3 trades: {roi_ex_3:.2%}")
    
    # Clipping analysis
    clipped_signals = signals[
        (signals['model_probability'] <= 0.0001) | 
        (signals['model_probability'] >= 0.9999)
    ]
    lines.append("\n### Model Output Clipping")
    lines.append(f"- Total signals: {len(signals)}")
    lines.append(f"- Clipped signals (0 or 1): {len(clipped_signals)} ({(len(clipped_signals)/len(signals) if len(signals)>0 else 0):.2%})")
    
    # Trade clipping
    clipped_trades = trades[
        (trades['model_probability'] <= 0.0001) | 
        (trades['model_probability'] >= 0.9999)
    ]
    unclipped_trades = trades[
        (trades['model_probability'] > 0.0001) & 
        (trades['model_probability'] < 0.9999)
    ]
    
    lines.append(f"- Clipped trades: {len(clipped_trades)}")
    lines.append(f"- Unclipped trades: {len(unclipped_trades)}")
    
    if len(clipped_trades) > 0:
        res_clipped = clipped_trades[clipped_trades['settlement_outcome'].isin(['WIN', 'LOSS'])]
        if len(res_clipped) > 0:
            roi_clipped = res_clipped['pnl_usd'].sum() / (len(res_clipped) * 5.0)
            lines.append(f"- Clipped trades ROI: {roi_clipped:.2%}")
            
    if len(unclipped_trades) > 0:
        res_unclipped = unclipped_trades[unclipped_trades['settlement_outcome'].isin(['WIN', 'LOSS'])]
        if len(res_unclipped) > 0:
            roi_unclipped = res_unclipped['pnl_usd'].sum() / (len(res_unclipped) * 5.0)
            lines.append(f"- Unclipped trades ROI: {roi_unclipped:.2%}")
            
    # Net worth lead analysis
    has_nw = trades[trades['token_net_worth_lead'].notna()]
    no_nw = trades[trades['token_net_worth_lead'].isna()]
    
    lines.append("\n### Net Worth Feature Missingness")
    lines.append(f"- Trades WITH net worth lead: {len(has_nw)}")
    lines.append(f"- Trades WITHOUT net worth lead: {len(no_nw)}")
    
    if len(has_nw) > 0:
        res_has = has_nw[has_nw['settlement_outcome'].isin(['WIN', 'LOSS'])]
        if len(res_has) > 0:
            roi_has = res_has['pnl_usd'].sum() / (len(res_has) * 5.0)
            lines.append(f"- WITH net worth ROI: {roi_has:.2%}")
            
    if len(no_nw) > 0:
        res_no = no_nw[no_nw['settlement_outcome'].isin(['WIN', 'LOSS'])]
        if len(res_no) > 0:
            roi_no = res_no['pnl_usd'].sum() / (len(res_no) * 5.0)
            lines.append(f"- WITHOUT net worth ROI: {roi_no:.2%}")
            
    lines.append("")
    return lines

def main():
    lines = ["# Clean Holdout Audit Report\n"]
    lines.append("This report covers the backtest on the 21 pure holdout matches (unseen during train and validation).")
    lines.append("Production gates enforced: 420-2400s game time, spread <= 0.05, book_age_ms <= 5000, confirmation enabled, one trade per match.\n")
    
    lines.extend(analyze_trades("reports/clean_holdout_audit/th_0.01", 0.01))
    lines.extend(analyze_trades("reports/clean_holdout_audit/th_0.02", 0.02))
    
    # 0.01 vs 0.02 Comparison
    t01 = pd.read_csv("reports/clean_holdout_audit/th_0.01/model_value_v1_trades.csv")
    t02 = pd.read_csv("reports/clean_holdout_audit/th_0.02/model_value_v1_trades.csv")
    
    lines.append("## 0.01 vs 0.02 Paired Comparison on Holdout")
    if not t01.empty and not t02.empty:
        t01_keys = set(t01['match_id'].astype(str))
        t02_keys = set(t02['match_id'].astype(str))
        
        common = t01_keys.intersection(t02_keys)
        only_01 = t01_keys - t02_keys
        only_02 = t02_keys - t01_keys
        
        lines.append(f"- Common matches traded: {len(common)}")
        lines.append(f"- Matches traded only in 0.01: {len(only_01)}")
        lines.append(f"- Matches traded only in 0.02: {len(only_02)}")
        
        if len(common) > 0:
            c1 = t01[t01['match_id'].astype(str).isin(common)].sort_values('match_id')
            c2 = t02[t02['match_id'].astype(str).isin(common)].sort_values('match_id')
            
            # align them
            c1 = c1.set_index('match_id')
            c2 = c2.set_index('match_id')
            
            ask_imp = (c2['entry_ask'] - c1['entry_ask']).mean()
            clv_diff = (c1['clv_1200s'] - c2['clv_1200s']).mean()
            
            lines.append(f"- Avg ask improvement (0.01 over 0.02): {ask_imp:.4f}")
            lines.append(f"- Avg CLV_1200s delta (0.01 over 0.02): {clv_diff:.4f}")
            
    with open("reports/clean_holdout_audit/report.md", "w") as f:
        f.write("\n".join(lines))
    print("Report written to reports/clean_holdout_audit/report.md")

if __name__ == "__main__":
    main()
