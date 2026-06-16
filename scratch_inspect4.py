#!/usr/bin/env python3
"""Part 4: attrition, coverage, models, market characterization."""
import json, os
os.chdir("/home/tstuv/dota-poly-signal-pnl-asd")
import pandas as pd

SEP = "=" * 70

def load_json(path):
    with open(path) as f:
        return json.load(f)

# ── 10a. attrition_waterfall.json ──
print(SEP)
print("10a. attrition_waterfall.json")
d = load_json("reports/attrition_waterfall.json")
print(json.dumps(d, indent=2, default=str))

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
print(json.dumps(d, indent=2, default=str))

# ── 12. market_mapping_audit summary ──
print(SEP)
print("12. market_mapping_audit.csv SUMMARY")
df = pd.read_csv("reports/market_mapping_audit.csv")
print("rows={}, cols={}".format(len(df), list(df.columns)))
print("nulls:")
print(df.isnull().sum().to_string())
for col in ["mapping_method", "mapping_confidence", "has_match_id", "has_outcome", "has_book_events", "attrition_reason"]:
    if col in df.columns:
        print("--- {} vc ---".format(col))
        print(df[col].value_counts().head(20).to_string())
# How many mapped?
if "match_id" in df.columns:
    mapped = df["match_id"].notna().sum()
    print("Markets with match_id: {} / {}".format(mapped, len(df)))
if "has_match_id" in df.columns:
    print("has_match_id True: {}".format((df["has_match_id"] == True).sum()))

# ── 13-14. Models ──
print(SEP)
print("13. models/model_b/")
for f in sorted(os.listdir("models/model_b")):
    fp = os.path.join("models/model_b", f)
    print("  {} ({:,} bytes)".format(f, os.path.getsize(fp)))
    if f.endswith(".json"):
        d = load_json(fp)
        print(json.dumps(d, indent=2, default=str)[:1500])

print(SEP)
print("14. models/model_b_v2/")
for f in sorted(os.listdir("models/model_b_v2")):
    fp = os.path.join("models/model_b_v2", f)
    print("  {} ({:,} bytes)".format(f, os.path.getsize(fp)))
    if f.endswith(".json"):
        d = load_json(fp)
        # Just top-level metrics
        if isinstance(d, dict):
            for k in ["selected_alpha", "train", "validation"]:
                if k in d:
                    print("  {}: {}".format(k, str(d[k])[:200]))
            if "alpha_sweep" in d and isinstance(d["alpha_sweep"], list):
                for item in d["alpha_sweep"][:2]:
                    a = item.get("alpha")
                    t = item.get("train", {})
                    v = item.get("validation", {})
                    print("  alpha={}: train_brier={}, val_brier={}, train_ll={}, val_ll={}".format(
                        a, t.get("brier"), v.get("brier"), t.get("log_loss"), v.get("log_loss")))
    elif f.endswith(".pkl"):
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
            print("  {}".format(info))
        except Exception as e:
            print("  pkl error: {}".format(e))

# ── 3. market_characterization.json ──
print(SEP)
print("3. market_characterization.json")
d = load_json("reports/market_characterization.json")
print(json.dumps(d, indent=2, default=str)[:5000])

print(SEP)
print("DONE PART 4")
