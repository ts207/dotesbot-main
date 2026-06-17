import os
import csv
import json

def _safe_float(val, default=0.0):
    try:
        return float(val) if val else default
    except (ValueError, TypeError):
        return default

def load_settlement_outcomes() -> dict:
    outcomes = {}
    
    # 1. Check strategy_outcomes.csv (token_id -> won=True/False)
    try:
        if os.path.exists("logs/strategy_outcomes.csv"):
            with open("logs/strategy_outcomes.csv", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    tok = row.get("token_id")
                    won = row.get("won")
                    if tok and won:
                        outcomes[tok] = 1.0 if won.lower() == "true" else 0.0
    except Exception:
        pass

    # 2. Check shadow_outcomes_cache.json (condition_id or match_id?)
    # But usually tokens are resolved in settlement_shadow.csv directly
    
    # 3. Check settlement_shadow.csv (token_id -> resolution)
    try:
        if os.path.exists("logs/settlement_shadow.csv"):
            with open("logs/settlement_shadow.csv", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    tok = row.get("token_id")
                    res = row.get("resolution")
                    if tok and res:
                        if res.upper() == "YES":
                            outcomes[tok] = 1.0
                        elif res.upper() == "NO":
                            outcomes[tok] = 0.0
    except Exception:
        pass
        
    return outcomes

def enrich_exit_observations(input_path: str, output_path: str):
    if not os.path.exists(input_path):
        return
        
    outcomes = load_settlement_outcomes()
    
    with open(input_path, "r", newline="") as infile:
        reader = list(csv.DictReader(infile))
        if not reader:
            return
            
    fieldnames = list(reader[0].keys())
    
    new_fields = [
        "settlement_price", 
        "settlement_pnl_usd", 
        "active_exit_delta_usd", 
        "active_exit_delta_roi", 
        "exit_helped", 
        "settlement_status"
    ]
    for f in new_fields:
        if f not in fieldnames:
            fieldnames.append(f)
            
    with open(output_path, "w", newline="") as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()
        
        for row in reader:
            tok = row.get("token_id")
            if tok in outcomes:
                price = outcomes[tok]
                row["settlement_status"] = "resolved"
                row["settlement_price"] = f"{price:.1f}"
                
                shares = _safe_float(row.get("shares"))
                cost = _safe_float(row.get("cost_usd"))
                actual_pnl = _safe_float(row.get("actual_pnl_usd"))
                
                settlement_pnl = (shares * price) - cost
                row["settlement_pnl_usd"] = f"{settlement_pnl:.4f}"
                
                delta_usd = actual_pnl - settlement_pnl
                row["active_exit_delta_usd"] = f"{delta_usd:.4f}"
                
                if cost > 0:
                    delta_roi = delta_usd / cost
                    row["active_exit_delta_roi"] = f"{delta_roi:.4f}"
                else:
                    row["active_exit_delta_roi"] = ""
                    
                row["exit_helped"] = "True" if delta_usd > 0 else "False"
            else:
                row["settlement_status"] = "unknown"
                row["settlement_price"] = ""
                row["settlement_pnl_usd"] = ""
                row["active_exit_delta_usd"] = ""
                row["active_exit_delta_roi"] = ""
                row["exit_helped"] = ""
                
            writer.writerow(row)

if __name__ == "__main__":
    enrich_exit_observations(
        "logs/exit_policy_observations.csv",
        "logs/exit_policy_observations_enriched.csv"
    )
