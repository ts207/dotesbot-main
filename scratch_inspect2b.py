#!/usr/bin/env python3
"""Part 2b: targeted reads for missing sections only."""
import json, os, sys, traceback
os.chdir("/home/tstuv/dota-poly-signal-pnl-asd")
import pandas as pd

SEP = "=" * 70

def load_json(path):
    with open(path) as f:
        return json.load(f)

# ── 1. bot_performance_backtest ──
print(SEP)
print("1. bot_performance_backtest_2026_06_07.json")
d = load_json("reports/bot_performance_backtest_2026_06_07.json")
print(json.dumps(d, indent=2, default=str)[:6000])

# ── 2. locked_set_summary.json ──
print(SEP)
print("2. locked_set_summary.json")
d = load_json("reports/locked_set_summary.json")
print(json.dumps(d, indent=2, default=str)[:3000])

# ── 2b. locked_set_reconciliation.csv ──
print(SEP)
print("2b. locked_set_reconciliation.csv")
df = pd.read_csv("reports/locked_set_reconciliation.csv")
print("rows={}, cols={}".format(len(df), list(df.columns)))
print(df.dtypes.to_string())
print("---nulls---")
print(df.isnull().sum().to_string())
for col in df.columns:
    if df[col].nunique() <= 25:
        print("--- {} vc ---".format(col))
        print(df[col].value_counts().to_string())
print("--- head ---")
print(df.head(5).to_string())

# ── 3. market_characterization.json ──
print(SEP)
print("3. market_characterization.json")
d = load_json("reports/market_characterization.json")
print(json.dumps(d, indent=2, default=str)[:4000])

# ── 4. polymarket_discovery_summary.json ──
print(SEP)
print("4. polymarket_discovery_summary.json")
d = load_json("reports/polymarket_discovery_summary.json")
print(json.dumps(d, indent=2, default=str)[:3000])

# ── 5. LEAKAGE ──
print(SEP)
print("5. dataset_leakage_checks.json (FULL)")
d = load_json("reports/dataset_leakage_checks.json")
print(json.dumps(d, indent=2, default=str))

# ── 6. train_validation_split_report.json ──
print(SEP)
print("6. train_validation_split_report.json")
d = load_json("reports/train_validation_split_report.json")
print(json.dumps(d, indent=2, default=str)[:3000])

print(SEP)
print("6b. train_validation_split_v2_report.json")
d = load_json("reports/train_validation_split_v2_report.json")
print(json.dumps(d, indent=2, default=str)[:3000])

# ── 7a. shadow_summary.csv ──
print(SEP)
print("7a. shadow_summary.csv")
df = pd.read_csv("reports/shadow_summary.csv")
print("rows={}, cols={}".format(len(df), list(df.columns)))
# Show summary by decision
if "decision" in df.columns:
    print("--- decision vc ---")
    print(df["decision"].value_counts().to_string())
if "event_type" in df.columns:
    print("--- event_type vc ---")
    print(df["event_type"].value_counts().to_string())
# Show rows where count > 5
big = df[df["count"] > 5] if "count" in df.columns else df
print("--- rows with count>5 ---")
print(big.to_string())

# ── 7b. shadow_report.md ──
print(SEP)
print("7b. shadow_report.md")
with open("reports/shadow_report.md") as f:
    print(f.read()[:5000])

print(SEP)
print("DONE PART 2b")
