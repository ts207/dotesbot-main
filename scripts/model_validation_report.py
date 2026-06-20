import sqlite3
import csv
import json
import os
from collections import defaultdict

def main():
    os.makedirs("reports", exist_ok=True)
    out_file = "reports/model_validation_v0.md"
    
    # 1. Read strategy_signals.csv
    signals_by_match = defaultdict(list)
    if os.path.exists("logs/strategy_signals.csv"):
        with open("logs/strategy_signals.csv", "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("strategy") == "MODEL_VALUE_EDGE":
                    match_id = row.get("match_id")
                    signals_by_match[match_id].append(row)
    
    # 2. Read live_positions from logs/state.sqlite and logs/state_v2.sqlite
    entries = []
    
    # Check state.sqlite
    if os.path.exists("logs/state.sqlite"):
        conn = sqlite3.connect("logs/state.sqlite")
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT raw_json FROM live_positions WHERE strategy_kind = 'MODEL_VALUE_EDGE' OR strategy_family = 'VALUE'")
            for row in cursor.fetchall():
                pos = json.loads(row[0])
                if pos.get("strategy_kind") == "MODEL_VALUE_EDGE" or pos.get("event_type") == "MODEL_VALUE_EDGE":
                    entries.append(pos)
        except sqlite3.OperationalError:
            pass
        conn.close()

    # Check state_v2.sqlite (paper positions)
    if os.path.exists("logs/state_v2.sqlite"):
        conn = sqlite3.connect("logs/state_v2.sqlite")
        try:
            cursor = conn.cursor()
            for table in ["positions", "closed_positions"]:
                cursor.execute(f"SELECT raw_json FROM {table} WHERE mode = 'paper' OR mode LIKE '%paper%'")
                for row in cursor.fetchall():
                    pos = json.loads(row[0])
                    if pos.get("strategy_kind") == "MODEL_VALUE_EDGE" or pos.get("event_type") == "MODEL_VALUE_EDGE":
                        entries.append(pos)
        except sqlite3.OperationalError:
            pass
        conn.close()

    # Deduplicate entries by position_id
    entries_by_id = {e["position_id"]: e for e in entries}
    
    entries_by_match = defaultdict(list)
    for e in entries_by_id.values():
        entries_by_match[e.get("match_id")].append(e)

    lines = []
    lines.append("# Model Validation Report (v0)")
    lines.append("")
    
    total_signals = sum(len(sigs) for sigs in signals_by_match.values())
    total_entries = len(entries_by_id)
    
    lines.append(f"**Total MODEL_VALUE_EDGE Signals:** {total_signals}")
    lines.append(f"**Total MODEL_VALUE_EDGE Entries:** {total_entries}")
    lines.append("")
    
    lines.append("## Signals by Match")
    
    expected_pnl = 0.0
    for match_id, sigs in signals_by_match.items():
        lines.append(f"### Match: {match_id}")
        lines.append(f"- **Signals generated:** {len(sigs)}")
        for i, s in enumerate(sigs):
            lines.append(f"  - Signal {i+1}: Side {s.get('side')} | Token: {s.get('token_id')} | Edge: {s.get('edge')} | Lead: {s.get('token_net_worth_lead')} | Score Margin: {s.get('token_score_margin')} | Ver: {s.get('model_version')}")
        
        match_entries = entries_by_match.get(match_id, [])
        lines.append(f"- **Entries made:** {len(match_entries)}")
        for e in match_entries:
            mode = e.get("paper_mode", e.get("mode", "live"))
            cost = e.get("cost_usd", e.get("size_usd", 0.0))
            if cost is None:
                cost = 0.0
            edge = e.get("entry_edge", 0.0)
            if edge is None:
                edge = 0.0
            pnl_contribution = float(cost) * float(edge)
            expected_pnl += pnl_contribution
            
            lines.append(f"  - Entry [{mode}]: Side {e.get('side')} | Token {e.get('token_id')} | Cost ${cost:.2f} | Edge at entry: {edge}")

        lines.append("")

    lines.append("## Summary")
    lines.append(f"- **Expected PnL at entry:** ${expected_pnl:.2f}")

    with open(out_file, "w") as f:
        f.write("\n".join(lines))
    print(f"Report written to {out_file}")

if __name__ == "__main__":
    main()
