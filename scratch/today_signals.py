import json
import datetime
for f in ['logs/value_v1_shadow_forward_decisions.jsonl', 'logs/market_disagreement_alpha_shadow_decisions.jsonl']:
    try:
        entries = []
        with open(f) as file:
            for line in file:
                d = json.loads(line)
                if d.get('decision') in ['WOULD_ENTER', 'WOULD_TRADE']:
                    # some timestamp fields might be decision_ts, others timestamp_utc
                    ts = d.get('decision_ts') or d.get('timestamp_utc')
                    if ts:
                        if isinstance(ts, str):
                            dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        else:
                            dt = datetime.datetime.fromtimestamp(ts/1e9, tz=datetime.timezone.utc)
                        if dt.date() in [datetime.date(2026, 6, 9), datetime.date(2026, 6, 8)]:
                            entries.append(f"[{f.split('/')[1]}] {dt.isoformat()} Match {d.get('match_id')} Side {d.get('side')} Edge {d.get('edge', d.get('executable_edge'))}")
        for e in entries: print(e)
        print(f"{f} - Total signals today: {len(entries)}\n")
    except Exception as e:
        print(e)
