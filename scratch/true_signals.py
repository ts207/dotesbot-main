import json
import datetime
for f in ['logs/value_v1_shadow_forward_decisions.jsonl', 'logs/market_disagreement_alpha_shadow_decisions.jsonl']:
    try:
        entries = 0
        with open(f) as file:
            for line in file:
                d = json.loads(line)
                entered = False
                if d.get('decision') in ['WOULD_ENTER', 'WOULD_TRADE']: entered = True
                if d.get('alpha_would_enter') is True: entered = True
                if entered:
                    ts = d.get('decision_ts') or d.get('timestamp_utc')
                    if ts:
                        if isinstance(ts, str):
                            dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        else:
                            dt = datetime.datetime.fromtimestamp(ts/1e9, tz=datetime.timezone.utc)
                        if dt.date() in [datetime.date(2026, 6, 9), datetime.date(2026, 6, 8)]:
                            entries += 1
                            if f == 'logs/market_disagreement_alpha_shadow_decisions.jsonl':
                                print(f"[{f.split('/')[1]}] {dt.isoformat()} Match {d.get('match_id')} alpha_rule {d.get('alpha_rule_id')} Ask {d.get('entry_ask')} Fair {d.get('fair')} Edge {d.get('edge')}")
        print(f"{f} - Total true signals: {entries}\n")
    except Exception as e:
        print(e)
