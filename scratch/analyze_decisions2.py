import json
from datetime import datetime, timezone

data = [json.loads(l) for l in open('logs/value_v1_shadow_forward_decisions.jsonl')]

# Per match: show the "best" opportunity - highest edge seen
print("=== Best Opportunity Per Match ===")
for mid in ['8844054970', '8844132483', '8844244719', '8844308689']:
    match_rows = [d for d in data if d['match_id'] == mid]
    # get rows where we had an ask
    with_ask = [d for d in match_rows if d.get('entry_ask') is not None]
    if with_ask:
        best = max(with_ask, key=lambda d: d.get('edge') or -99)
        print(f"\nMatch {mid}:")
        print(f"  game_time={best['game_time_sec']} lead={best['radiant_lead']} ask={best['entry_ask']} edge={best.get('edge')} fair={best.get('fair')} decision={best['decision']} reason={best['reason']}")
    else:
        # Show why no ask
        sample = match_rows[0]
        print(f"\nMatch {mid}: NO ASK AVAILABLE at all")
        print(f"  First eval: game_time={sample['game_time_sec']} lead={sample['radiant_lead']} best_bid={sample.get('best_bid')} reason={sample['reason']}")
        last = match_rows[-1]
        print(f"  Last eval:  game_time={last['game_time_sec']} lead={last['radiant_lead']} best_bid={last.get('best_bid')} reason={last['reason']}")

# Also show the markets.yaml has these matches mapped
print("\n\n=== What shadow monitor DID see ===")
print("Total evaluated decisions:", len(data))
print("Unique match_ids:", set(d['match_id'] for d in data))
print("Most recent decision ts:", datetime.fromtimestamp(data[-1]['decision_ts']/1e9, tz=timezone.utc))
print("Markets these came from: the monitor pulled raw_snapshots.csv on startup and evaluated all historical rows")
