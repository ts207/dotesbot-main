import json
from datetime import datetime, timezone

data = [json.loads(l) for l in open('logs/value_v1_shadow_forward_decisions.jsonl')]
enters = [d for d in data if d['decision'] == 'WOULD_ENTER']
print(f"Total decisions: {len(data)}")
print(f"WOULD_ENTER count: {len(enters)}")

# Also show all unique matches evaluated
matches = {}
for d in data:
    mid = d['match_id']
    if mid not in matches:
        matches[mid] = {'decisions': 0, 'would_enter': 0, 'reasons': set()}
    matches[mid]['decisions'] += 1
    if d['decision'] == 'WOULD_ENTER':
        matches[mid]['would_enter'] += 1
    matches[mid]['reasons'].add(d.get('reason', d['decision']))

print("\nMatch-level summary:")
for mid, info in sorted(matches.items()):
    print(f"  {mid}: {info['decisions']} evals, {info['would_enter']} WOULD_ENTER, reasons={info['reasons']}")

if enters:
    print("\nWOULD_ENTER details:")
    for e in enters:
        ts = datetime.fromtimestamp(e['decision_ts']/1e9, tz=timezone.utc)
        print(json.dumps({k:v for k,v in e.items() if k != 'survival_overlay_reason'}, indent=2))
