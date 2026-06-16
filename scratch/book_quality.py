import yaml, json
from collections import defaultdict, Counter

with open('markets.yaml') as f:
    data = yaml.safe_load(f)
mlist = data if isinstance(data, list) else data.get('markets', [])
print('Total markets:', len(mlist))

# show ones from Jun 8-9 by looking at auto_mapped_at_utc
recent = [m for m in mlist if '2026-06-08' in str(m.get('auto_mapped_at_utc','')) or '2026-06-09' in str(m.get('auto_mapped_at_utc',''))]
print('Recent bound (Jun 8-9):', len(recent))
for m in recent:
    print(' ', m.get('name','')[:65], '|', m.get('match_id'), '|', m.get('market_type'))

# Also show book quality by checking match IDs
# For each bound match, check what's in book_events
import csv
print('\n--- Book quality for recent bound matches ---')
match_ids_recent = list(set(str(m.get('match_id','')) for m in recent if m.get('match_id')))
token_to_market = {}
for m in mlist:
    for key in ('yes_token_id', 'no_token_id'):
        tok = str(m.get(key, ''))
        if tok:
            token_to_market[tok] = m.get('name','')[:55]

book_by_token = defaultdict(list)
with open('logs/book_events.csv') as f:
    reader = csv.DictReader(f)
    for row in reader:
        asset = row.get('asset_id','')
        if asset in token_to_market:
            book_by_token[asset].append(row)

if book_by_token:
    print(f"{'market':<55} {'ticks':>6} {'asks':>6} {'min_ask':>8} {'max_bid':>8}")
    for asset, rows in sorted(book_by_token.items(), key=lambda x: -len(x[1])):
        ask_rows = [r for r in rows if r.get('best_ask') and float(r['best_ask']) > 0]
        bid_rows = [r for r in rows if r.get('best_bid') and float(r['best_bid']) > 0]
        min_ask = f"{min(float(r['best_ask']) for r in ask_rows):.3f}" if ask_rows else 'NONE'
        max_bid = f"{max(float(r['best_bid']) for r in bid_rows):.3f}" if bid_rows else 'NONE'
        name = token_to_market[asset]
        print(f"  {name:<55} {len(rows):>6} {len(ask_rows):>6} {min_ask:>8} {max_bid:>8}")
else:
    print("  No book events found for recent bound markets (tokens not in book_events.csv)")
    print("  This is the root cause of missing_ask — book feed not covering these tokens")
