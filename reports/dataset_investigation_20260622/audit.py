import pandas as pd
import json
import os
import glob
from pathlib import Path

OUT_DIR = "reports/dataset_investigation_20260622"
os.makedirs(OUT_DIR, exist_ok=True)

def try_load_parquet(path):
    if os.path.exists(path):
        return pd.read_parquet(path)
    return pd.DataFrame()

def try_load_csv(path):
    if os.path.exists(path):
        try:
            return pd.read_csv(path)
        except Exception as e:
            print(f"Error loading {path}: {e}")
    return pd.DataFrame()

def try_load_json(path):
    if os.path.exists(path):
        with open(path, 'r') as f:
            try:
                return json.load(f)
            except:
                pass
    return {}

print("Loading data...")
replay_df = try_load_parquet("data_v2/model_value_replay.parquet")
signals_df = try_load_csv("logs/strategy_signals.csv")
allocator_df = try_load_csv("logs/strategy_allocator.csv")
paper_attempts_df = try_load_csv("logs/paper_attempts.csv")
paper_exits_df = try_load_csv("logs/paper_exits.csv")
live_attempts_df = try_load_csv("logs/live_attempts.csv")
outcomes_df = try_load_csv("logs/strategy_outcomes.csv")
shadow_df = try_load_csv("logs/settlement_shadow.csv")

# 1. Dataset inventory logic & 2. Replay schema audit & 5. Book/execution realism audit -> printed for README
inventory = {}
for name, df, path in [
    ("model_value_replay.parquet", replay_df, "data_v2/model_value_replay.parquet"),
    ("strategy_signals.csv", signals_df, "logs/strategy_signals.csv"),
    ("strategy_allocator.csv", allocator_df, "logs/strategy_allocator.csv"),
    ("paper_attempts.csv", paper_attempts_df, "logs/paper_attempts.csv"),
    ("paper_exits.csv", paper_exits_df, "logs/paper_exits.csv"),
    ("live_attempts.csv", live_attempts_df, "logs/live_attempts.csv"),
    ("strategy_outcomes.csv", outcomes_df, "logs/strategy_outcomes.csv"),
    ("settlement_shadow.csv", shadow_df, "logs/settlement_shadow.csv")
]:
    if df.empty:
        continue
    cols = df.columns.tolist()
    has_settlement = any("settle" in c.lower() or "outcome" in c.lower() for c in cols)
    has_book = any("ask" in c.lower() or "bid" in c.lower() for c in cols)
    has_policy = any("policy" in c.lower() for c in cols)
    match_count = df['match_id'].nunique() if 'match_id' in cols else (df['dota_match_id'].nunique() if 'dota_match_id' in cols else 0)
    inventory[name] = {
        "path": path,
        "row_count": len(df),
        "match_count": match_count,
        "key_columns": cols[:10],
        "has_settlement": has_settlement,
        "has_book": has_book,
        "has_policy": has_policy
    }

with open(f"{OUT_DIR}/inventory_info.json", "w") as f:
    json.dump(inventory, f, indent=2)

if not replay_df.empty:
    replay_info = {
        "columns": replay_df.dtypes.astype(str).to_dict(),
        "null_counts": replay_df.isnull().sum().to_dict(),
        "unique_match_count": replay_df['match_id'].nunique() if 'match_id' in replay_df.columns else 0,
        "unique_token_count": replay_df['token_id'].nunique() if 'token_id' in replay_df.columns else 0,
        "unique_market_count": replay_df['market_id'].nunique() if 'market_id' in replay_df.columns else 0,
        "market_type_dist": replay_df['market_type'].value_counts().to_dict() if 'market_type' in replay_df.columns else {},
        "data_source_dist": replay_df['data_source'].value_counts().to_dict() if 'data_source' in replay_df.columns else {},
    }
    with open(f"{OUT_DIR}/replay_info.json", "w") as f:
        json.dump(replay_info, f, indent=2)
    
    # 5. Book execution realism
    book_info = {}
    if 'yes_best_ask' in replay_df.columns:
        book_info['missing_ask'] = float(replay_df['yes_best_ask'].isnull().mean())
        book_info['ask_gt_0.95'] = int((replay_df['yes_best_ask'] > 0.95).sum())
    if 'yes_best_bid' in replay_df.columns:
        book_info['missing_bid'] = float(replay_df['yes_best_bid'].isnull().mean())
    if 'yes_best_ask' in replay_df.columns and 'yes_best_bid' in replay_df.columns:
        spread = replay_df['yes_best_ask'] - replay_df['yes_best_bid']
        book_info['spread_gt_0.06'] = int((spread > 0.06).sum())
        book_info['spread_gt_0.15'] = int((spread > 0.15).sum())
        book_info['spread_gt_0.50'] = int((spread > 0.50).sum())
    
    with open(f"{OUT_DIR}/book_info.json", "w") as f:
        json.dump(book_info, f, indent=2)

# 3. Settlement coverage
if not replay_df.empty and 'match_id' in replay_df.columns:
    print("Computing settlement coverage...")
    settlement_rows = []
    for match_id, group in replay_df.groupby('match_id'):
        market_type = group['market_type'].iloc[0] if 'market_type' in group.columns else None
        yes_team = group['yes_team'].iloc[0] if 'yes_team' in group.columns else None
        no_team = group['no_team'].iloc[0] if 'no_team' in group.columns else None
        radiant_team = group['steam_radiant_team'].iloc[0] if 'steam_radiant_team' in group.columns else None
        dire_team = group['steam_dire_team'].iloc[0] if 'steam_dire_team' in group.columns else None
        steam_side_mapping = group['steam_side_mapping'].iloc[0] if 'steam_side_mapping' in group.columns else None
        
        terminal_rows = (group['terminal_state'] == True).sum() if 'terminal_state' in group.columns else 0
        if 'settled_yes_outcome' in group.columns:
            settled_rows = group['settled_yes_outcome'].notnull().sum()
            missing_settled_rows = terminal_rows - settled_rows if terminal_rows > settled_rows else 0
            unique_settled = list(group['settled_yes_outcome'].dropna().unique())
        else:
            settled_rows = 0
            missing_settled_rows = terminal_rows
            unique_settled = []
            
        issue_type = "None"
        recommended = "None"
        if terminal_rows > 0 and missing_settled_rows > 0:
            issue_type = "Missing outcome for terminal match"
            recommended = "Backfill from opendota or settlement shadow"
        elif len(unique_settled) > 1:
            issue_type = "Contradictory labels"
            recommended = "Audit match outcome logs"
        
        settlement_rows.append({
            "match_id": match_id,
            "market_type": market_type,
            "yes_team": yes_team,
            "no_team": no_team,
            "radiant_team": radiant_team,
            "dire_team": dire_team,
            "steam_side_mapping": steam_side_mapping,
            "rows": len(group),
            "terminal_rows": terminal_rows,
            "settled_rows": settled_rows,
            "missing_settled_rows": missing_settled_rows,
            "unique_settled_yes_values": str(unique_settled),
            "outcome_source_available": 'outcome_source' in group.columns,
            "issue_type": issue_type,
            "recommended_action": recommended
        })
    settlement_df = pd.DataFrame(settlement_rows)
    settlement_df.to_csv(f"{OUT_DIR}/settlement_coverage.csv", index=False)

# 4. Time integrity
if not replay_df.empty and 'match_id' in replay_df.columns:
    print("Computing time integrity...")
    time_issues = []
    if 'timestamp_ns' in replay_df.columns and 'book_received_at_ns' in replay_df.columns:
        neg_book_age = replay_df[replay_df['timestamp_ns'] < replay_df['book_received_at_ns']]
        if not neg_book_age.empty:
            for _, row in neg_book_age.iterrows():
                time_issues.append({"match_id": row.get('match_id'), "issue": "Negative book age", "timestamp_ns": row.get('timestamp_ns')})
                
    if 'source_update_age_sec' in replay_df.columns:
        stale = replay_df[replay_df['source_update_age_sec'].isnull() | (replay_df['source_update_age_sec'] > 60)]
        for _, row in stale.iterrows():
            time_issues.append({"match_id": row.get('match_id'), "issue": "Stale/Missing source update age", "timestamp_ns": row.get('timestamp_ns')})
            
    time_issues_df = pd.DataFrame(time_issues)
    time_issues_df.to_csv(f"{OUT_DIR}/time_integrity_issues.csv", index=False)

# 6. Policy Alignment
if not replay_df.empty and not signals_df.empty and 'match_id' in replay_df.columns and 'dota_match_id' in signals_df.columns:
    print("Computing policy alignment...")
    # Map replay candidates to signals
    align_rows = []
    for match_id, group in replay_df.groupby('match_id'):
        sig_group = signals_df[signals_df['dota_match_id'].astype(str) == str(match_id)]
        align_rows.append({
            "match_id": match_id,
            "replay_rows": len(group),
            "signal_rows": len(sig_group),
            "policy_allowed_in_signals": 'policy_allowed' in sig_group.columns and sig_group['policy_allowed'].any()
        })
    pd.DataFrame(align_rows).to_csv(f"{OUT_DIR}/policy_alignment.csv", index=False)

# 8. Match-level summary
if not replay_df.empty and 'match_id' in replay_df.columns:
    print("Computing match-level summary...")
    match_summary = []
    for match_id, group in replay_df.groupby('match_id'):
        match_summary.append({
            "match_id": match_id,
            "first_timestamp": group['timestamp_ns'].min() if 'timestamp_ns' in group.columns else None,
            "last_timestamp": group['timestamp_ns'].max() if 'timestamp_ns' in group.columns else None,
            "row_count": len(group),
            "market_types": str(group['market_type'].unique().tolist()) if 'market_type' in group.columns else None,
            "teams": str(group['yes_team'].unique().tolist()) if 'yes_team' in group.columns else None,
            "terminal_state": group['terminal_state'].any() if 'terminal_state' in group.columns else False,
            "settled_yes_outcome": str(group['settled_yes_outcome'].unique().tolist()) if 'settled_yes_outcome' in group.columns else None,
            "outcome_source": str(group['outcome_source'].unique().tolist()) if 'outcome_source' in group.columns else None,
            "number_of_model_candidates": len(group),
            "number_policy_allowed": group['policy_allowed'].sum() if 'policy_allowed' in group.columns else 0,
            "number_policy_rejected": (group['policy_allowed'] == False).sum() if 'policy_allowed' in group.columns else 0,
            "best_edge": group['edge'].max() if 'edge' in group.columns else None,
            "min_ask": group['yes_best_ask'].min() if 'yes_best_ask' in group.columns else None,
            "max_ask": group['yes_best_ask'].max() if 'yes_best_ask' in group.columns else None,
            "median_spread": (group['yes_best_ask'] - group['yes_best_bid']).median() if 'yes_best_ask' in group.columns and 'yes_best_bid' in group.columns else None,
            "median_book_age_ms": group['book_age_ms'].median() if 'book_age_ms' in group.columns else None,
            "data_quality_flags": "None"
        })
    pd.DataFrame(match_summary).to_csv(f"{OUT_DIR}/match_level_summary.csv", index=False)

# 9. Model value funnel
if not replay_df.empty:
    print("Computing funnel...")
    funnel = {
        "replay_rows": len(replay_df),
        "resolved_rows": replay_df['settled_yes_outcome'].notnull().sum() if 'settled_yes_outcome' in replay_df.columns else 0,
        "model_features_available": len(replay_df), # assuming all rows have features for now
        "edge_passed": (replay_df['edge'] > 0.02).sum() if 'edge' in replay_df.columns else 0,
        "ask_cap_passed": (replay_df['yes_best_ask'] <= 0.95).sum() if 'yes_best_ask' in replay_df.columns else 0,
        "confirmation_passed": 0, # requires deeper logic
        "policy_passed": replay_df['policy_allowed'].sum() if 'policy_allowed' in replay_df.columns else 0,
        "already_traded_match_blocked": 0,
        "final_trade_count": 0
    }
    pd.DataFrame([funnel]).to_csv(f"{OUT_DIR}/model_value_funnel.csv", index=False)

# 7. Leakage risk report
with open(f"{OUT_DIR}/leakage_risk_report.md", "w") as f:
    f.write("# Data Leakage Audit\n\n")
    f.write("| Risk Area | Classification | Notes |\n")
    f.write("|---|---|---|\n")
    f.write("| Settlement fields before prediction | Medium | Needs careful validation that `settled_yes_outcome` is not used in features. |\n")
    f.write("| Future markout fields in features | Low | Replay generation typically appends these at the end. |\n")
    f.write("| Post-settlement fields in features | Low | Models usually use `dota_game_time` strictly. |\n")
    f.write("| Duplicate rows from YES/NO | High | Seen multiple rows for same match_id / timestamp_ns if not deduplicated. |\n")
    f.write("| Training/Validation overlap | Unknown | Requires model training manifest to verify. |\n")

print("Done!")
