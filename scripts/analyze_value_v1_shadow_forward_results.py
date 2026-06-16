#!/usr/bin/env python3
"""
Analyze results from the Shadow-Forward Monitor.
Summarizes hypothetical decisions and operational health.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from datetime import datetime, timezone

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

def main():
    decisions_path = REPO_ROOT / "logs" / "value_v1_shadow_forward_decisions.jsonl"
    if not decisions_path.exists():
        print(f"No decisions log found at {decisions_path}")
        return 1
        
    entries = []
    with open(decisions_path, "r") as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line))
                
    if not entries:
        print("No entries found in decisions log.")
        return 0
        
    # Analysis
    total = len(entries)
    decisions = Counter(e["decision"] for e in entries)
    reasons = Counter(e["reason"] for e in entries)
    matches = set(e["match_id"] for e in entries)
    would_enters = [e for e in entries if e["decision"] == "WOULD_ENTER"]
    
    # Operational health
    causality_violations = sum(1 for e in entries if not e.get("causality_valid", True))

    # Episode clustering for VALUE v1
    signal_list = []
    for e in would_enters:
        ts_ns = e.get("decision_wall_time_ns")
        if not ts_ns and "timestamp_utc" in e:
            try:
                dt = datetime.fromisoformat(e["timestamp_utc"].replace("Z", "+00:00"))
                ts_ns = int(dt.timestamp() * 1e9)
            except:
                ts_ns = 0
                
        signal_list.append({
            "match_id": e.get("match_id", ""),
            "market_id": e.get("market_id", ""),
            "side": "YES",
            "rule_id": "VALUE_V1",
            "ts_ns": ts_ns or 0
        })
        
    try:
        from shadow_episodes import compute_episode_metrics, cluster_episodes
    except ImportError:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from shadow_episodes import compute_episode_metrics, cluster_episodes

    ep_metrics_180 = compute_episode_metrics(signal_list, 180)
    
    # Calculate PnLs
    try:
        from forward_pnl import compute_episode_pnls
        episodes = cluster_episodes(would_enters, 180)
        pnls = compute_episode_pnls(episodes, REPO_ROOT)
    except ImportError:
        pnls = []
    
    # Calculate aggregate PnLs for the summary
    agg_pnl = {
        "episodes_scored": len(pnls),
        "episodes_settled": 0,
        "total_settled_pnl": 0.0,
        "avg_pnl_30s": 0.0,
        "avg_pnl_60s": 0.0,
        "avg_pnl_300s": 0.0,
        "avg_pnl_to_convergence": 0.0
    }
    
    if pnls:
        settled = [p["pnl_settle"] for p in pnls if p.get("pnl_settle") is not None]
        agg_pnl["episodes_settled"] = len(settled)
        agg_pnl["total_settled_pnl"] = round(sum(settled), 3)
        
        def avg(key):
            vals = [p[key] for p in pnls if p.get(key) is not None]
            return round(sum(vals)/len(vals), 3) if vals else None
            
        agg_pnl["avg_pnl_30s"] = avg("pnl_30s")
        agg_pnl["avg_pnl_60s"] = avg("pnl_60s")
        agg_pnl["avg_pnl_300s"] = avg("pnl_300s")
        agg_pnl["avg_pnl_to_convergence"] = avg("pnl_to_convergence")
    
    report_json = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_entries": total,
        "unique_matches": len(matches),
        "decision_breakdown": dict(decisions),
        "reject_reason_breakdown": dict(reasons),
        "would_enter_count": len(would_enters),
        "episode_metrics": ep_metrics_180,
        "pnl_metrics": agg_pnl,
        "episode_pnls": pnls,
        "causality_violations": causality_violations,
        "operational_checks": {
            "7_day_monitoring_complete": False, # Placeholder
            "zero_causality_violations": causality_violations == 0,
        }
    }
    
    # Summary MD
    summary_md = f"""# Value v1 Shadow-Forward Summary
    
## Monitoring Status
- **Generated At**: {report_json['generated_at']}
- **Total Polls Evaluated**: {total}
- **Unique Matches**: {len(matches)}
- **Causality Violations**: {causality_violations}

## Decision Breakdown
- **WOULD_ENTER**: {decisions.get('WOULD_ENTER', 0)}
- **Unique Episodes**: {ep_metrics_180['unique_episodes']}
- **Avg Signals/Episode**: {ep_metrics_180['avg_signals_per_episode']}
- **WOULD_SKIP**: {decisions.get('WOULD_SKIP', 0)}
- **WOULD_REJECT**: {decisions.get('WOULD_REJECT', 0)}
- **DUPLICATE_BLOCKED**: {decisions.get('DUPLICATE_POSITION_BLOCKED', 0)}

## Reject Reasons
"""
    for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
        summary_md += f"- **{reason}**: {count}\n"
        
    summary_md += f"""
## PnL Metrics
- **Episodes Scored**: {agg_pnl['episodes_scored']}
- **Episodes Settled**: {agg_pnl['episodes_settled']}
- **Total Settled PnL**: {agg_pnl['total_settled_pnl']}
- **Avg PnL 30s**: {agg_pnl['avg_pnl_30s']}
- **Avg PnL 60s**: {agg_pnl['avg_pnl_60s']}
- **Avg PnL 300s**: {agg_pnl['avg_pnl_300s']}
- **Avg PnL to Convergence**: {agg_pnl['avg_pnl_to_convergence']}

## Operational Readiness Review
- Continuous Monitoring: IN_PROGRESS
- Zero Causality Violations: {"PASS" if causality_violations == 0 else "FAIL"}
- Validated Baseline Reconciliation: PENDING_FORWARD_DATA
"""

    out_dir = REPO_ROOT / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    
    with open(out_dir / "value_v1_shadow_forward_summary.json", "w") as f:
        json.dump(report_json, f, indent=2)
        
    with open(out_dir / "value_v1_shadow_forward_summary.md", "w") as f:
        f.write(summary_md)
        
    print(summary_md)
    return 0

if __name__ == "__main__":
    sys.exit(main())
