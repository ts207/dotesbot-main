import json
with open('logs/market_disagreement_alpha_shadow_decisions.jsonl') as f:
    for line in f:
        d = json.loads(line)
        if d.get('alpha_would_enter') is True:
            print(f"Match {d['match_id']} | Edge {d['edge']} | Ask {d['entry_ask']} | Book Age ms {d['book_age_ms']} | Networth Lead {d['networth_lead']} | Game Time {d['game_time_sec']}")
