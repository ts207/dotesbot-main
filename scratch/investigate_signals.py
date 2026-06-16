import json
import datetime
for f in ['logs/value_v1_shadow_forward_decisions.jsonl', 'logs/market_disagreement_alpha_shadow_decisions.jsonl']:
    try:
        print(f"--- {f} close calls ---")
        with open(f) as file:
            for line in file:
                d = json.loads(line)
                r = d.get('reject_reason') or d.get('alpha_reject_reason') or d.get('reason')
                if r in ['alpha_edge', 'fair_too_low', 'edge_too_low']:
                    ts = d.get('decision_ts') or d.get('timestamp_utc')
                    if ts:
                        if isinstance(ts, str):
                            dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        else:
                            dt = datetime.datetime.fromtimestamp(ts/1e9, tz=datetime.timezone.utc)
                        print(f"[{dt.isoformat()}] Match: {d.get('match_id')} | Reason: {r} | Edge: {d.get('edge', d.get('executable_edge'))} | Fair: {d.get('fair')} | Ask: {d.get('entry_ask')}")
    except Exception as e:
        print(e)
