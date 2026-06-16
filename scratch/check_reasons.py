import json
from collections import Counter

for f in ['logs/value_v1_shadow_forward_decisions.jsonl', 'logs/market_disagreement_alpha_shadow_decisions.jsonl']:
    try:
        reasons = Counter()
        with open(f) as file:
            for line in file:
                d = json.loads(line)
                r = d.get('reject_reason') or d.get('alpha_reject_reason') or d.get('reason')
                reasons[r] += 1
        print(f"--- {f} ---")
        for r, c in reasons.most_common(10):
            print(f"{r}: {c}")
    except Exception as e:
        print(e)
