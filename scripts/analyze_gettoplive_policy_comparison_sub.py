import bisect
import math
from collections import defaultdict, Counter

def analyze_policies(snapshots, markets, book, outcomes, outcome_sources, config, params, manual_windows):
    results = []
    
    # 1. We need to iterate over snapshots.
    for match_id, rows in snapshots.items():
        mapping = markets.get(match_id)
        if not mapping:
            continue
            
        yes_token = str(mapping["yes_token_id"])
        no_token = str(mapping["no_token_id"])
        # Use existing value logic for won, etc
        
        # We need a lookback queue to compute 15s, 30s, 60s deltas.
        history = [] # (ns, lead)
        
        for idx, row in enumerate(rows):
            ns = int(row.get("received_at_ns") or 0)
            lead = row.get("radiant_lead")
            if lead is None:
                continue
            lead = int(lead)
            
            history.append((ns, lead))
            
            # Find trailing leads
            lead_15s_ago = get_past_lead(history, ns, 15_000_000_000)
            lead_30s_ago = get_past_lead(history, ns, 30_000_000_000)
            lead_60s_ago = get_past_lead(history, ns, 60_000_000_000)
            
            if lead_15s_ago is None or lead_30s_ago is None or lead_60s_ago is None:
                continue
                
            delta_15s = lead - lead_15s_ago
            delta_30s = lead - lead_30s_ago
            delta_60s = lead - lead_60s_ago
            
            # calculate quadrants and metrics
            # ...
            
