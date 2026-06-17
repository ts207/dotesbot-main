import csv
with open("logs/strategy_signals.csv") as f:
    reader = csv.DictReader(f)
    rows = list(reader)
    for row in rows[-20:]:
        print(f"Match: {row.get('match_id')}, Market: {row.get('market_name', 'unknown')}, Strategy: {row.get('strategy_kind', '')} - {row.get('skip_reason', '')} - {row.get('reason', '')}")
