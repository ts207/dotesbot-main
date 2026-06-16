#!/usr/bin/env python3
"""Full data analysis pass on today's shadow forward data."""
import json
import csv
import sys
from datetime import datetime, timezone
from collections import defaultdict, Counter

# --- 1. DECISIONS LOG ---
decisions = []
with open('logs/value_v1_shadow_forward_decisions.jsonl') as f:
    for line in f:
        try:
            decisions.append(json.loads(line))
        except:
            pass

enters = [d for d in decisions if d['decision'] == 'WOULD_ENTER']
rejects = [d for d in decisions if d['decision'] == 'WOULD_REJECT']
skips   = [d for d in decisions if d['decision'] == 'WOULD_SKIP']

print("=" * 60)
print("SHADOW FORWARD DECISIONS SUMMARY")
print("=" * 60)
print(f"Total decisions:   {len(decisions)}")
print(f"  WOULD_ENTER:     {len(enters)}")
print(f"  WOULD_SKIP:      {len(skips)}")
print(f"  WOULD_REJECT:    {len(rejects)}")

if decisions:
    first_ts = datetime.fromtimestamp(decisions[0]['decision_ts']/1e9, tz=timezone.utc)
    last_ts  = datetime.fromtimestamp(decisions[-1]['decision_ts']/1e9, tz=timezone.utc)
    print(f"\nTime range: {first_ts.strftime('%Y-%m-%d %H:%M UTC')} → {last_ts.strftime('%Y-%m-%d %H:%M UTC')}")

print("\n--- Reject Reasons ---")
reason_counts = Counter(d.get('reason', d['decision']) for d in decisions)
for reason, count in reason_counts.most_common():
    print(f"  {reason:<40} {count}")

# --- Per match analysis ---
print("\n--- Match-Level Analysis ---")
by_match = defaultdict(list)
for d in decisions:
    by_match[d['match_id']].append(d)

print(f"{'match_id':<15} {'evals':>5} {'ENTER':>5} {'max_edge':>10} {'max_lead':>9} {'has_ask':>7} {'top_reject'}")
for mid, rows in sorted(by_match.items()):
    enters_m = [r for r in rows if r['decision'] == 'WOULD_ENTER']
    edges = [r.get('edge') for r in rows if r.get('edge') is not None]
    leads = [abs(r.get('radiant_lead', 0)) for r in rows]
    has_ask = any(r.get('entry_ask') is not None for r in rows)
    top_r = Counter(r.get('reason', r['decision']) for r in rows if r['decision'] != 'WOULD_ENTER').most_common(1)
    top_reject = top_r[0][0] if top_r else '-'
    max_edge = f"{max(edges):.3f}" if edges else 'N/A'
    max_lead = max(leads) if leads else 0
    print(f"  {mid:<15} {len(rows):>5} {len(enters_m):>5} {max_edge:>10} {max_lead:>9} {str(has_ask):>7}  {top_reject}")

# --- WOULD_ENTER details ---
if enters:
    print("\n\n=== WOULD_ENTER EPISODES ===")
    for e in enters:
        ts = datetime.fromtimestamp(e['decision_ts']/1e9, tz=timezone.utc)
        print(f"  {ts.strftime('%Y-%m-%d %H:%M:%S UTC')} | match={e['match_id']} | side={e.get('side')} | ask={e.get('entry_ask')} | edge={e.get('edge'):.3f} | fair={e.get('fair'):.3f} | lead={e.get('radiant_lead')} | gt={e.get('game_time_sec')}s | stake=${e.get('would_stake_usd')}")

# --- 2. RAW SNAPSHOTS summary ---
print("\n\n" + "=" * 60)
print("RAW SNAPSHOTS ANALYSIS (today's data)")
print("=" * 60)
snap_by_match = defaultdict(list)
with open('logs/raw_snapshots.csv') as f:
    reader = csv.DictReader(f)
    for row in reader:
        snap_by_match[row['match_id']].append(row)

print(f"Total snapshot rows: {sum(len(v) for v in snap_by_match.values())}")
print(f"Unique match IDs:    {len(snap_by_match)}")

# Filter to only matches that appear in our markets.yaml
with open('markets.yaml') as f:
    markets_raw = f.read()

tradeable = {mid for mid in snap_by_match if mid in markets_raw}
print(f"Matches in markets.yaml: {len(tradeable)}")

print("\n--- Snapshot coverage for tradeable matches ---")
print(f"{'match_id':<15} {'snaps':>6} {'gt_range':>18} {'max_lead':>10} {'data_source'}")
for mid in sorted(tradeable):
    rows = snap_by_match[mid]
    gts = [int(r['game_time_sec']) for r in rows if r['game_time_sec']]
    leads = [abs(int(r['radiant_lead'])) for r in rows if r['radiant_lead']]
    sources = Counter(r['data_source'] for r in rows)
    gt_range = f"{min(gts)}s → {max(gts)}s" if gts else 'N/A'
    max_lead = max(leads) if leads else 0
    src = ','.join(f"{k}({v})" for k,v in sources.most_common(2))
    print(f"  {mid:<15} {len(rows):>6} {gt_range:>18} {max_lead:>10}  {src}")

# --- 3. Book events ---
print("\n\n" + "=" * 60)
print("BOOK EVENTS ANALYSIS")
print("=" * 60)
book_by_asset = defaultdict(list)
with open('logs/book_events.csv') as f:
    reader = csv.DictReader(f)
    for row in reader:
        book_by_asset[row['asset_id']].append(row)

print(f"Unique assets tracked: {len(book_by_asset)}")
print(f"Total book ticks:      {sum(len(v) for v in book_by_asset.values())}")

# Cross reference with markets yaml to find relevant assets  
# Read markets yaml properly
import subprocess
result = subprocess.run(['python3', '-c', '''
import yaml
with open("markets.yaml") as f:
    data = yaml.safe_load(f)
if isinstance(data, list):
    for m in data:
        yes = m.get("yes_token_id", "")
        no  = m.get("no_token_id",  "")
        name = m.get("name", "")[:55]
        print(f"{yes}|{no}|{name}")
'''], capture_output=True, text=True)

token_to_name = {}
if result.returncode == 0:
    for line in result.stdout.splitlines():
        parts = line.split("|")
        if len(parts) == 3:
            yes_tok, no_tok, name = parts
            if yes_tok: token_to_name[yes_tok] = name + " (YES)"
            if no_tok:  token_to_name[no_tok]  = name + " (NO)"

print(f"Known tokens from markets.yaml: {len(token_to_name)}")
matched_assets = {a: rows for a, rows in book_by_asset.items() if a in token_to_name}
print(f"Book ticks for known markets: {sum(len(v) for v in matched_assets.values())} across {len(matched_assets)} tokens")

if matched_assets:
    print("\n--- Book quality for known tokens ---")
    print(f"{'market (truncated)':<55} {'ticks':>6} {'has_ask':>8} {'min_ask':>8} {'max_bid':>8}")
    for asset, rows in sorted(matched_assets.items(), key=lambda x: -len(x[1])):
        has_ask = any(r.get('best_ask') and float(r['best_ask']) > 0 for r in rows)
        asks = [float(r['best_ask']) for r in rows if r.get('best_ask') and float(r['best_ask']) > 0]
        bids = [float(r['best_bid']) for r in rows if r.get('best_bid') and float(r['best_bid']) > 0]
        min_ask = f"{min(asks):.3f}" if asks else 'NONE'
        max_bid = f"{max(bids):.3f}" if bids else 'NONE'
        name = token_to_name[asset][:55]
        print(f"  {name:<55} {len(rows):>6} {str(has_ask):>8} {min_ask:>8} {max_bid:>8}")

print("\nDone.")
