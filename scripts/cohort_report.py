#!/usr/bin/env python3
import os
import csv
import pandas as pd
from collections import Counter

def load_csv(path):
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()

def generate_report():
    signals = load_csv("logs/strategy_signals.csv")
    value_att = load_csv("logs/value_attempts.csv")
    dswing_att = load_csv("logs/dswing_attempts.csv")
    paper_trades = load_csv("logs/paper_trades.csv")
    
    # Cohorts
    cohorts = {}
    
    # 1. VALUE_EDGE
    if not value_att.empty:
        v_sigs = value_att[value_att['would_trade'] == True]
        v_rejs = value_att[value_att['would_trade'] == False]
        cohorts['VALUE_EDGE'] = {
            'candidates': len(value_att),
            'rejects': len(v_rejs),
            'signals': len(v_sigs),
            'reject_reasons': Counter(v_rejs['reject_reason']).most_common(5),
            'avg_ask': v_sigs['ask'].mean(),
            'avg_fair': v_sigs['fair_price'].mean(),
            'avg_edge': v_sigs['edge'].mean(),
        }

    # 2. EVENT_TRIGGERED_VALUE (CONTINUATION / REVERSAL)
    if not signals.empty:
        for name, sub in [('ETV_CONTINUATION', signals[signals['is_continuation'] == True]),
                          ('ETV_REVERSAL', signals[signals['is_reversal'] == True])]:
            s_sigs = sub[sub['would_trade'] == True]
            s_rejs = sub[sub['would_trade'] == False]
            cohorts[name] = {
                'candidates': len(sub),
                'rejects': len(s_rejs),
                'signals': len(s_sigs),
                'reject_reasons': Counter(s_rejs['reject_reason']).most_common(5),
                'avg_ask': pd.to_numeric(s_sigs['ask'], errors='coerce').mean(),
                'avg_fair': pd.to_numeric(s_sigs['fair_price'], errors='coerce').mean(),
                'avg_edge': pd.to_numeric(s_sigs['edge'], errors='coerce').mean(),
                'avg_fair_delta': pd.to_numeric(s_sigs['fair_delta'], errors='coerce').mean(),
            }

    # 3. DSWING
    if not dswing_att.empty:
        d_sigs = dswing_att[dswing_att['would_trade'] == True]
        d_rejs = dswing_att[dswing_att['would_trade'] == False]
        cohorts['DSWING'] = {
            'candidates': len(dswing_att),
            'rejects': len(d_rejs),
            'signals': len(d_sigs),
            'reject_reasons': Counter(d_rejs['reject_reason']).most_common(5),
            'avg_ask': d_sigs['ask'].mean(),
            'avg_fair': d_sigs['series_fair'].mean(),
            'avg_edge': d_sigs['edge'].mean(),
        }

    # Add trades/ROI if paper_trades exists
    if not paper_trades.empty:
        for cname in cohorts:
            # Map cohort to paper_trades strategy_kind / event_type
            if cname == 'VALUE_EDGE':
                t_sub = paper_trades[paper_trades['strategy_kind'].str.upper() == 'VALUE']
            elif cname == 'ETV_CONTINUATION':
                t_sub = paper_trades[(paper_trades['strategy_kind'].str.upper() == 'EVENT_TRIGGERED_VALUE') & (paper_trades['entry_derived_state_flags'].str.contains('continuation', na=False, case=False))]
            elif cname == 'ETV_REVERSAL':
                t_sub = paper_trades[(paper_trades['strategy_kind'].str.upper() == 'EVENT_TRIGGERED_VALUE') & (paper_trades['entry_derived_state_flags'].str.contains('reversal', na=False, case=False))]
            elif cname == 'DSWING':
                t_sub = paper_trades[paper_trades['strategy_kind'].str.upper() == 'DSWING']
            else:
                continue
                
            entries = t_sub[t_sub['action'] == 'entry']
            exits = t_sub[t_sub['action'] == 'exit']
            cohorts[cname]['entries'] = len(entries)
            cohorts[cname]['exits'] = len(exits)
            if not exits.empty:
                cohorts[cname]['roi'] = exits['roi'].mean()
                cohorts[cname]['pnl'] = exits['pnl_usd'].sum()
                cohorts[cname]['exit_reasons'] = Counter(exits['exit_reason']).most_common(5)

    # Print Report
    print("=== STRATEGY COHORT REPORT ===")
    for name, data in cohorts.items():
        print(f"\nCohort: {name}")
        print(f"  Candidates: {data['candidates']}")
        print(f"  Rejects:    {data['rejects']} ({data.get('reject_reasons')})")
        print(f"  Signals:    {data['signals']}")
        print(f"  Avg Ask:    {data.get('avg_ask', 0):.4f}")
        print(f"  Avg Fair:   {data.get('avg_fair', 0):.4f}")
        print(f"  Avg Edge:   {data.get('avg_edge', 0):.4f}")
        if 'avg_fair_delta' in data:
            print(f"  Avg Delta:  {data['avg_fair_delta']:.4f}")
        
        if 'entries' in data:
            print(f"  Entries:    {data['entries']}")
            print(f"  Exits:      {data['exits']}")
            print(f"  ROI:        {data.get('roi', 0):.2%}")
            print(f"  PnL:       ${data.get('pnl', 0):.2f}")
            print(f"  Exit Reasons: {data.get('exit_reasons')}")

if __name__ == "__main__":
    generate_report()
