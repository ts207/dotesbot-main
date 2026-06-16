import yaml, json, csv
from collections import defaultdict, Counter
from datetime import datetime, timezone

# Load markets
with open('markets.yaml') as f:
    data = yaml.safe_load(f)
mlist = data if isinstance(data, list) else data.get('markets', [])

# Recent bound markets (Jun 8-9)
recent = [m for m in mlist if '2026-06-08' in str(m.get('auto_mapped_at_utc','')) or '2026-06-09' in str(m.get('auto_mapped_at_utc',''))]
print(f"=== RECENT BOUND MARKETS (Jun 8-9): {len(recent)} ===")
for m in recent:
    print(f"  {m.get('name','')[:65]}")
    print(f"    match_id={m.get('match_id')} type={m.get('market_type')} mapped={m.get('auto_mapped_at_utc')}")

# Now load book events to find which ones had a real 2-sided book during the game
print("\n=== BOOK COVERAGE FOR THESE MATCHES ===")

# Build token -> market mapping for recent only
token_to_market = {}
for m in recent:
    for key in ('yes_token_id', 'no_token_id'):
        tok = str(m.get(key, ''))
        if tok and tok != 'None':
            token_to_market[tok] = {
                'name': m.get('name','')[:60],
                'side': 'YES' if key == 'yes_token_id' else 'NO',
                'match_id': str(m.get('match_id','')),
            }

print(f"Tokens to look for: {len(token_to_market)}")

# Scan book events
book_data = defaultdict(lambda: {'ticks': 0, 'asks': [], 'bids': []})
with open('logs/book_events.csv') as f:
    reader = csv.DictReader(f)
    for row in reader:
        asset = row.get('asset_id','')
        if asset in token_to_market:
            b = book_data[asset]
            b['ticks'] += 1
            ask = float(row['best_ask']) if row.get('best_ask') and row['best_ask'] else 0
            bid = float(row['best_bid']) if row.get('best_bid') and row['best_bid'] else 0
            if ask > 0: b['asks'].append(ask)
            if bid > 0: b['bids'].append(bid)
            # record timestamps
            if 'timestamps' not in b:
                b['timestamps'] = []
            b['timestamps'].append(row.get('timestamp_utc',''))

if not book_data:
    print("\nNONE of the recent bound markets appeared in book_events.csv!")
    print("This means the REST book refresh is NOT polling these specific tokens.")
    print("\nLet's check what the book_events.csv actually IS tracking:")
    
    # Sample the book_events to see what's in there
    tracked_assets = set()
    with open('logs/book_events.csv') as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i > 5000: break
            tracked_assets.add(row.get('asset_id',''))
    print(f"  Sample of {len(tracked_assets)} assets in book_events (first 5000 rows)")
    # Check if ANY of our known tokens are in there
    matches = [t for t in tracked_assets if t in token_to_market]
    print(f"  Overlap with our {len(token_to_market)} tokens: {len(matches)}")
    
    # Check ALL market tokens against the full book events
    all_tokens = set()
    for m in mlist:
        for key in ('yes_token_id', 'no_token_id'):
            tok = str(m.get(key, ''))
            if tok and tok != 'None':
                all_tokens.add(tok)
    print(f"\nAll tokens in markets.yaml: {len(all_tokens)}")
    
    all_book_assets = set()
    with open('logs/book_events.csv') as f:
        reader = csv.DictReader(f)
        for row in reader:
            all_book_assets.add(row.get('asset_id',''))
    
    overlap = all_tokens & all_book_assets
    print(f"All book_events assets: {len(all_book_assets)}")
    print(f"Markets tokens found in book_events: {len(overlap)}")
    
    if overlap:
        print("\nSample matched tokens:")
        for tok in list(overlap)[:5]:
            market = next((m for m in mlist if str(m.get('yes_token_id','')) == tok or str(m.get('no_token_id','')) == tok), None)
            if market:
                print(f"  {market.get('name','')[:60]} | mapped={market.get('auto_mapped_at_utc','N/A')}")
else:
    print(f"\n{'market':<60} {'side':>5} {'ticks':>6} {'asks':>6} {'min_ask':>8} {'max_bid':>8}")
    for tok, b in sorted(book_data.items(), key=lambda x: -x[1]['ticks']):
        info = token_to_market[tok]
        min_ask = f"{min(b['asks']):.3f}" if b['asks'] else 'NONE'
        max_bid = f"{max(b['bids']):.3f}" if b['bids'] else 'NONE'
        print(f"  {info['name']:<60} {info['side']:>5} {b['ticks']:>6} {len(b['asks']):>6} {min_ask:>8} {max_bid:>8}")
