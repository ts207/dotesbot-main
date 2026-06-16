#!/usr/bin/env python3
"""Part 3: remaining sections."""
import json, os, traceback
os.chdir("/home/tstuv/dota-poly-signal-pnl-asd")
import pandas as pd

SEP = "=" * 70

def load_json(path):
    with open(path) as f:
        return json.load(f)

# ── 7b. shadow_report.md ──
print(SEP)
print("7b. shadow_report.md")
with open("reports/shadow_report.md") as f:
    print(f.read()[:5000])

# ── 8. model_b_validation_report.json ──
print(SEP)
print("8. model_b_validation_report.json")
d = load_json("reports/model_b_validation_report.json")
print(json.dumps(d, indent=2, default=str)[:5000])

# ── 9. model_b_v2_validation_report.json ──
print(SEP)
print("9. model_b_v2_validation_report.json")
d = load_json("reports/model_b_v2_validation_report.json")
print(json.dumps(d, indent=2, default=str)[:5000])

# ── 10a. attrition_waterfall.json ──
print(SEP)
print("10a. attrition_waterfall.json")
d = load_json("reports/attrition_waterfall.json")
print(json.dumps(d, indent=2, default=str)[:4000])

# ── 10b. attrition_waterfall_rows.csv ──
print(SEP)
print("10b. attrition_waterfall_rows.csv")
df = pd.read_csv("reports/attrition_waterfall_rows.csv")
print("rows={}, cols={}".format(len(df), list(df.columns)))
print(df.to_string())

# ── 11. dota_universe_coverage_audit.json ──
print(SEP)
print("11. dota_universe_coverage_audit.json")
d = load_json("reports/dota_universe_coverage_audit.json")
print(json.dumps(d, indent=2, default=str)[:5000])

# ── 12. market_mapping_audit.csv ──
print(SEP)
print("12. market_mapping_audit.csv DETAILS")
df = pd.read_csv("reports/market_mapping_audit.csv")
print("rows={}, cols={}".format(len(df), list(df.columns)))
print("nulls:")
print(df.isnull().sum().to_string())
for col in ["mapping_method", "mapping_confidence", "market_type", "event_type"]:
    if col in df.columns:
        print("--- {} vc ---".format(col))
        print(df[col].value_counts().head(20).to_string())
print("--- head(5) ---")
print(df.head(5).to_string())

# ── 13. models/model_b/ ──
print(SEP)
print("13. models/model_b/")
for f in os.listdir("models/model_b"):
    fp = os.path.join("models/model_b", f)
    print("  {} ({:,} bytes)".format(f, os.path.getsize(fp)))
    if f.endswith(".json"):
        d = load_json(fp)
        print(json.dumps(d, indent=2, default=str)[:2000])

# ── 14. models/model_b_v2/ ──
print(SEP)
print("14. models/model_b_v2/")
for f in os.listdir("models/model_b_v2"):
    fp = os.path.join("models/model_b_v2", f)
    print("  {} ({:,} bytes)".format(f, os.path.getsize(fp)))
    if f.endswith(".json"):
        d = load_json(fp)
        print(json.dumps(d, indent=2, default=str)[:2000])
    elif f.endswith(".pkl"):
        import pickle
        try:
            with open(fp, "rb") as pf:
                obj = pickle.load(pf)
            print("  pkl type: {}".format(type(obj).__name__))
            if hasattr(obj, "coef_"):
                print("  coef_: {}".format(obj.coef_))
            if hasattr(obj, "intercept_"):
                print("  intercept_: {}".format(obj.intercept_))
            if hasattr(obj, "feature_names_in_"):
                print("  features: {}".format(list(obj.feature_names_in_)))
            if hasattr(obj, "classes_"):
                print("  classes: {}".format(obj.classes_))
        except Exception as e:
            print("  pkl error: {}".format(e))

# ── 3. market_characterization.json ──
print(SEP)
print("3. market_characterization.json")
d = load_json("reports/market_characterization.json")
print(json.dumps(d, indent=2, default=str)[:5000])

print(SEP)
print("DONE PART 3")
