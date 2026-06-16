import yaml, json, csv
from collections import defaultdict, Counter
from datetime import datetime, timezone

# Load markets
with open('markets.yaml') as f:
    data = yaml.safe_load(f)
mlist = data if isinstance(data, list) else data.get('markets', [])
recent = [m for m in mlist if '2026-06-08' in str(m.get('auto_mapped_at_utc','')) or '2026-06-09' in str(m.get('auto_mapped_at_utc',''))]

# Token -> market mapping
token_to_market = {}
for m in recent:
    for key in ('yes_token_id', 'no_token_id'):
        tok = str(m.get(key, ''))
        if tok and tok != 'None':
            token_to_market[tok] = {
                'name': m.get('name',''),
                'side': 'YES' if key == 'yes_token_id' else 'NO',
                'match_id': str(m.get('match_id','')),
                'market_type': m.get('market_type',''),
            }

# Load book events: find windows where the market had BOTH bid AND ask in reasonable range
# i.e., was actually tradeable (2-sided book)
print("=== TRADEABLE WINDOWS ANALYSIS ===")
print("(When did each market have a real 2-sided book with ask < 0.85?)\n")

book_by_token = defaultdict(list)
with open('logs/book_events.csv') as f:
    reader = csv.DictReader(f)
    for row in reader:
        asset = row.get('asset_id','')
        if asset in token_to_market:
            ask = float(row['best_ask']) if row.get('best_ask') and row['best_ask'] else None
            bid = float(row['best_bid']) if row.get('best_bid') and row['best_bid'] else None
            book_by_token[asset].append({
                'ts': row.get('timestamp_utc',''),
                'ask': ask,
                'bid': bid,
            })

# For each token, find when ask was in a buyable range (0.50 to 0.85) = actual opportunity
print(f"{'Market':<62} {'side':>4} | {'tradeable_ticks':>15} | {'min_ask':>8} | {'first_tradeable':>20}")
print("-" * 120)
for tok, rows in sorted(book_by_token.items(), key=lambda x: token_to_market[x[0]]['name']):
    info = token_to_market[tok]
    # Tradeable = has ask AND ask is between 0.50 and 0.85 (value bot entry zone)  
    tradeable = [r for r in rows if r['ask'] is not None and 0.50 <= r['ask'] <= 0.85]
    all_asks = [r['ask'] for r in rows if r['ask'] is not None]
    min_ask = min(all_asks) if all_asks else None
    first_tradeable = tradeable[0]['ts'][:19] if tradeable else 'NEVER'
    
    name_short = info['name'][:62]
    print(f"  {name_short:<62} {info['side']:>4} | {len(tradeable):>15} | {str(min_ask or 'N/A'):>8} | {first_tradeable}")

# Now the KEY question: did the shadow monitor's book_events have these during the game?
# The raw_snapshots show us when the game was live.
print("\n\n=== GAME STATE vs BOOK COVERAGE OVERLAP ===")
print("(Did book_events actually see this market DURING the game?)\n")

snap_by_match = defaultdict(list)
with open('logs/raw_snapshots.csv') as f:
    reader = csv.DictReader(f)
    for row in reader:
        snap_by_match[row['match_id']].append(row)

for m in recent:
    mid = str(m.get('match_id',''))
    name = m.get('name','')[:65]
    yes_tok = str(m.get('yes_token_id',''))
    no_tok  = str(m.get('no_token_id',''))
    
    snaps = snap_by_match.get(mid, [])
    if not snaps:
        print(f"  {name}")
        print(f"    match_id={mid} — NO STEAM SNAPSHOTS (match not seen in raw_snapshots.csv)")
        continue
    
    # Game time window
    gts = [int(r['game_time_sec']) for r in snaps if r.get('game_time_sec')]
    snap_times = [r.get('received_at_utc','') for r in snaps if r.get('received_at_utc')]
    snap_start = min(snap_times) if snap_times else 'N/A'
    snap_end   = max(snap_times) if snap_times else 'N/A'
    
    # Book events in game window for both tokens
    yes_rows = book_by_token.get(yes_tok, [])
    no_rows  = book_by_token.get(no_tok, [])
    
    # Tradeable YES ticks during game window
    yes_tradeable = [r for r in yes_rows if r['ask'] is not None and 0.50 <= r['ask'] <= 0.85 and snap_start <= r['ts'][:25] <= snap_end]
    no_tradeable  = [r for r in no_rows  if r['ask'] is not None and 0.50 <= r['ask'] <= 0.85 and snap_start <= r['ts'][:25] <= snap_end]
    
    print(f"  {name}")
    print(f"    GT range: {min(gts) if gts else '?'}s → {max(gts) if gts else '?'}s | Snaps: {len(snaps)}")
    print(f"    Book YES: {len(yes_rows)} ticks total, {len(yes_tradeable)} in tradeable window during game")
    print(f"    Book NO:  {len(no_rows)} ticks total, {len(no_tradeable)} in tradeable window during game")
    if yes_tradeable or no_tradeable:
        all_tradeable = yes_tradeable + no_tradeable
        best = min(all_tradeable, key=lambda r: r['ask'])
        print(f"    *** MISSED OPPORTUNITY: best ask={best['ask']:.3f} at {best['ts'][:19]} ***")
    print()
