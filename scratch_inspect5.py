#!/usr/bin/env python3
"""Part 5: just summaries for attrition, coverage, models, market char."""
import json, os
os.chdir("/home/tstuv/dota-poly-signal-pnl-asd")
import pandas as pd

SEP = "=" * 70

def load_json(path):
    with open(path) as f:
        return json.load(f)

# ── 10a. attrition_waterfall.json - just summary ──
print(SEP)
print("10a. attrition_waterfall.json")
d = load_json("reports/attrition_waterfall.json")
if isinstance(d, dict):
    for k, v in d.items():
        if k != "details" and not isinstance(v, list):
            print("  {}: {}".format(k, json.dumps(v, default=str)[:300]))
    # If details is a list, just count
    if "details" in d and isinstance(d["details"], list):
        print("  details: list of {} items".format(len(d["details"])))
    # Print summary/waterfall/counts type keys
    for k in ["summary", "waterfall", "counts", "stages", "buckets", "totals"]:
        if k in d:
            print("  [{}]: {}".format(k, json.dumps(d[k], indent=2, default=str)[:2000]))

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
# Print non-list items
for k, v in d.items():
    if not isinstance(v, list):
        print("  {}: {}".format(k, json.dumps(v, default=str)[:500]))
    else:
        print("  {}: list of {} items".format(k, len(v)))

# ── 12. market_mapping_audit.csv SUMMARY ──
print(SEP)
print("12. market_mapping_audit.csv SUMMARY")
df = pd.read_csv("reports/market_mapping_audit.csv")
print("rows={}, cols={}".format(len(df), list(df.columns)))
print("nulls:")
print(df.isnull().sum().to_string())
# Value counts for categorical columns
for col in df.columns:
    if df[col].dtype == 'object' or df[col].dtype == 'bool' or df[col].nunique() <= 20:
        print("--- {} (nuniq={}) ---".format(col, df[col].nunique()))
        print(df[col].value_counts().head(15).to_string())

# ── 13-14. Models ──
print(SEP)
print("13. models/model_b/")
for f in sorted(os.listdir("models/model_b")):
    fp = os.path.join("models/model_b", f)
    print("  {} ({:,} bytes)".format(f, os.path.getsize(fp)))
    if f.endswith(".json"):
        d = load_json(fp)
        # Just top-level scalar values
        for k, v in d.items():
            if isinstance(v, (str, int, float, bool, type(None))):
                print("    {}: {}".format(k, v))
            elif isinstance(v, dict) and len(v) <= 10:
                print("    {}: {}".format(k, json.dumps(v, default=str)[:200]))
            elif isinstance(v, list):
                print("    {}: list of {}".format(k, len(v)))

print(SEP)
print("14. models/model_b_v2/")
for f in sorted(os.listdir("models/model_b_v2")):
    fp = os.path.join("models/model_b_v2", f)
    print("  {} ({:,} bytes)".format(f, os.path.getsize(fp)))
    if f.endswith(".pkl"):
        import pickle
        try:
            with open(fp, "rb") as pf:
                obj = pickle.load(pf)
            t = type(obj).__name__
            info = "type={}".format(t)
            if hasattr(obj, "coef_"):
                info += " coef={}".format(obj.coef_.tolist())
            if hasattr(obj, "intercept_"):
                info += " intercept={}".format(obj.intercept_)
            if hasattr(obj, "feature_names_in_"):
                info += " features={}".format(list(obj.feature_names_in_))
            if hasattr(obj, "classes_"):
                info += " classes={}".format(obj.classes_.tolist())
            if hasattr(obj, "C"):
                info += " C={}".format(obj.C)
            print("    {}".format(info))
        except Exception as e:
            print("    pkl error: {}".format(e))
    elif f.endswith(".json"):
        d = load_json(fp)
        if isinstance(d, dict):
            # Just top-level keys and selected_alpha / pass_fail type info
            for k in ["selected_alpha", "pass_fail", "n_features", "features"]:
                if k in d:
                    print("    {}: {}".format(k, json.dumps(d[k], default=str)[:300]))
            if "alpha_sweep" in d and isinstance(d["alpha_sweep"], list):
                for item in d["alpha_sweep"][:2]:
                    a = item.get("alpha")
                    t = item.get("train", {})
                    v = item.get("validation", {})
                    print("    alpha={}: train(brier={}, ll={}) val(brier={}, ll={})".format(
                        a, t.get("brier"), t.get("log_loss"), v.get("brier"), v.get("log_loss")))

# ── 3. market_characterization.json ──
print(SEP)
print("3. market_characterization.json")
d = load_json("reports/market_characterization.json")
for k, v in d.items():
    if isinstance(v, (str, int, float, bool, type(None))):
        print("  {}: {}".format(k, v))
    elif isinstance(v, dict) and len(v) <= 15:
        print("  [{}]:".format(k))
        for sk, sv in v.items():
            print("    {}: {}".format(sk, str(sv)[:200]))
    elif isinstance(v, dict):
        print("  [{}]: {} keys".format(k, len(v)))
    elif isinstance(v, list):
        print("  {}: list of {}".format(k, len(v)))

# ── locked_set_summary.json ──
print(SEP)
print("2. locked_set_summary.json")
d = load_json("reports/locked_set_summary.json")
print(json.dumps(d, indent=2, default=str)[:3000])

print(SEP)
print("DONE PART 5")
