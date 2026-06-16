#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

def main():
    decisions_path = REPO_ROOT / "logs" / "market_disagreement_alpha_shadow_decisions.jsonl"
    if not decisions_path.exists():
        print(f"File not found: {decisions_path}")
        return

    # metrics per rule
    metrics = {
        "deep_discount_high_edge_leader_candidate": {
            "causality_violations": 0,
            "stale_book_enters": 0,
            "incremental_signals": 0,
            "signal_list": []
        },
        "market_lag_candidate": {
            "causality_violations": 0,
            "stale_book_enters": 0,
            "incremental_signals": 0,
            "signal_list": []
        }
    }

    with open(decisions_path, "r") as f:
        for line in f:
            if not line.strip(): continue
            try:
                row = json.loads(line)
            except:
                continue
                
            rule_id = row.get("alpha_rule_id")
            if rule_id not in metrics:
                continue
                
            m = metrics[rule_id]
            
            if row.get("alpha_would_enter"):
                # We need ts_ns to cluster. 
                # If decision_wall_time_ns exists use it, else parse timestamp_utc
                ts_ns = row.get("decision_wall_time_ns")
                if not ts_ns and "timestamp_utc" in row:
                    from datetime import datetime, timezone
                    try:
                        dt = datetime.fromisoformat(row["timestamp_utc"].replace("Z", "+00:00"))
                        ts_ns = int(dt.timestamp() * 1e9)
                    except:
                        ts_ns = 0
                        
                s = {
                    "match_id": row.get("match_id", ""),
                    "token_id": row.get("token_id", ""),
                    "entry_ask": row.get("entry_ask", None),
                    "market_id": row.get("market_id", ""),
                    "side": "YES", # We only buy YES internally
                    "rule_id": rule_id,
                    "ts_ns": ts_ns or 0,
                    "poll_ts": row.get("poll_ts", 0)
                }
                m["signal_list"].append(s)
                
                if not row.get("causality_valid", True):
                    m["causality_violations"] += 1
                if row.get("book_age_ms", 0) > 15000:
                    m["stale_book_enters"] += 1
                if row.get("incremental_vs_value_v1"):
                    m["incremental_signals"] += 1

    try:
        from shadow_episodes import compute_episode_metrics, cluster_episodes
    except ImportError:
        # If run from another dir, add to sys.path
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from shadow_episodes import compute_episode_metrics, cluster_episodes

    print("Market Disagreement Alpha Shadow Monitor Analysis\n")
    for rule_id, m in metrics.items():
        print(f"=== {rule_id} ===")
        
        # Calculate episode metrics
        episodes = cluster_episodes(m["signal_list"], 180)
        ep_metrics_180 = compute_episode_metrics(m["signal_list"], 180)
        
        try:
            from forward_pnl import compute_episode_pnls
            pnls = compute_episode_pnls(episodes, REPO_ROOT)
        except ImportError:
            pnls = []
            
        settled = [p["pnl_settle"] for p in pnls if p.get("pnl_settle") is not None]
        episodes_settled = len(settled)
        total_settled_pnl = sum(settled)
        
        def avg(key):
            vals = [p[key] for p in pnls if p.get(key) is not None]
            return round(sum(vals)/len(vals), 3) if vals else "N/A"
            
        print(f"  Total WOULD_ENTER signals: {ep_metrics_180['raw_would_enter_signals']}")
        print(f"  Unique Episodes: {ep_metrics_180['unique_episodes']}")
        print(f"  Episodes Settled: {episodes_settled}")
        print(f"  Total Settled PnL: {total_settled_pnl:.3f}")
        print(f"  Avg PnL 30s: {avg('pnl_30s')}")
        print(f"  Avg PnL 300s: {avg('pnl_300s')}")
        print(f"  Avg PnL to Convergence: {avg('pnl_to_convergence')}")
        print(f"  Avg Signals/Episode: {ep_metrics_180['avg_signals_per_episode']}")
        print(f"  Incremental signals (vs VALUE v1): {m['incremental_signals']}")
        print(f"  Causality Violations: {m['causality_violations']}")
        print(f"  Stale Book Enters: {m['stale_book_enters']}")
        print(f"  Top 5 Episode Share: {ep_metrics_180['top_5_episode_share']:.1%}")
        
        # Sensitivity checks (print but maybe not all details to keep it concise, or just standard output)
        ep_metrics_60 = compute_episode_metrics(m["signal_list"], 60)
        ep_metrics_300 = compute_episode_metrics(m["signal_list"], 300)
        print(f"  [Sensitivity] Episodes at 60s gap: {ep_metrics_60['unique_episodes']}")
        print(f"  [Sensitivity] Episodes at 300s gap: {ep_metrics_300['unique_episodes']}")
        
        # Promotion Checks
        ready = True
        reasons = []
        if m["causality_violations"] > 0:
            ready = False
            reasons.append("> 0 causality violations")
        if m["stale_book_enters"] > 0:
            ready = False
            reasons.append("> 0 stale book enters")
        if ep_metrics_180['unique_episodes'] < 20:
            ready = False
            reasons.append("< 20 unique episodes")
        if ep_metrics_180['top_5_episode_share'] > 0.60:
            ready = False
            reasons.append("Top 5 episode share > 60%")
            
        print(f"  PROMOTION_READY: {ready}")
        if not ready:
            print(f"  Reasons: {', '.join(reasons)}")
        print()

if __name__ == "__main__":
    main()
