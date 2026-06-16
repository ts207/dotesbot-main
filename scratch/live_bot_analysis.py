import csv, json
from datetime import datetime, timezone
from collections import defaultdict, Counter

# ---- 1. value_attempts.csv — what the LIVE BOT actually evaluated ----
print("=" * 65)
print("LIVE BOT VALUE ENGINE ATTEMPTS (value_attempts.csv)")
print("=" * 65)

rows = []
with open('logs/value_attempts.csv') as f:
    reader = csv.DictReader(f)
    for row in reader:
        rows.append(row)

print(f"Total rows: {len(rows)}")

# Unique matches
by_match = defaultdict(list)
for r in rows:
    by_match[r['match_id']].append(r)

print(f"Unique matches: {len(by_match)}")

# Time range
times = [r['timestamp_utc'] for r in rows if r.get('timestamp_utc')]
print(f"Time range: {min(times)[:19]} → {max(times)[:19]}")

# Trades vs rejects
trades    = [r for r in rows if r.get('would_trade','').lower() == 'true']
rejects   = [r for r in rows if r.get('would_trade','').lower() != 'true']
print(f"\nWould-trade: {len(trades)}")
print(f"Rejected:    {len(rejects)}")

# Reject reasons
print("\n--- Reject Reason Distribution ---")
reasons = Counter(r.get('reject_reason','') for r in rejects)
for reason, count in reasons.most_common():
    print(f"  {reason:<45} {count}")

# ---- Key matches: Jun 8 European Pro League ----
target_matches = {
    '8843636057': 'VP.Prodigy vs BALU G1',
    '8843760302': 'VP.Prodigy vs BALU G2',
    '8843915671': 'summer bear vs Zero Tenacity G1',
    '8844054970': 'Zero Tenacity vs summer bear G2',
    '8844132483': 'VP vs Zero Tenacity G1',
    '8844244719': 'VP vs Zero Tenacity G2',
    '8844308689': 'VP vs Zero Tenacity G3',
}

print("\n\n--- Jun 8 EPL Match Coverage ---")
for mid, label in target_matches.items():
    rows_m = by_match.get(mid, [])
    if not rows_m:
        print(f"\n  {label} ({mid}): NOT IN value_attempts.csv")
        continue
    would_trade = [r for r in rows_m if r.get('would_trade','').lower() == 'true']
    print(f"\n  {label} ({mid}): {len(rows_m)} evals, {len(would_trade)} would-trade")
    r_reasons = Counter(r.get('reject_reason','') for r in rows_m if r.get('would_trade','').lower() != 'true')
    for reason, cnt in r_reasons.most_common(5):
        print(f"    reject: {reason} ({cnt}x)")
    # best opportunity
    with_ask = [r for r in rows_m if r.get('ask') and float(r['ask']) > 0]
    if with_ask:
        # Sort by edge descending
        best = sorted(with_ask, key=lambda r: float(r.get('edge') or -99), reverse=True)[0]
        print(f"    best ask seen: ask={best.get('ask')} edge={best.get('edge')} fair={best.get('fair_price')} lead={best.get('lead')} gt={best.get('game_time_sec')}s")
    # Show would_trade ones
    if would_trade:
        for wt in would_trade[:3]:
            print(f"    *** WOULD_TRADE: side={wt.get('side')} ask={wt.get('ask')} edge={wt.get('edge')} fair={wt.get('fair_price')} lead={wt.get('lead')} gt={wt.get('game_time_sec')}s sized=${wt.get('sized_usd')}")

# ---- All would_trade rows ----
print("\n\n" + "=" * 65)
print("ALL WOULD-TRADE SIGNALS FROM LIVE BOT")
print("=" * 65)
if trades:
    for t in sorted(trades, key=lambda r: r['timestamp_utc']):
        mid = t['match_id']
        label = target_matches.get(mid, mid)
        print(f"  {t['timestamp_utc'][:19]} | {label} | side={t.get('side')} ask={t.get('ask')} edge={t.get('edge')} fair={t.get('fair_price')} lead={t.get('lead')} gt={t.get('game_time_sec')}s sized=${t.get('sized_usd')}")
else:
    print("  No would-trade signals found.")

# ---- stream_delay analysis ----
print("\n\n--- Stream Delay Distribution (all rows) ---")
# pull stream delay from stdout.log would be complex; use book_age_ms as proxy
ages = [float(r['book_age_ms']) for r in rows if r.get('book_age_ms') and r['book_age_ms']]
if ages:
    import statistics
    print(f"  book_age_ms: min={min(ages):.0f} median={statistics.median(ages):.0f} max={max(ages):.0f} p95={sorted(ages)[int(len(ages)*0.95)]:.0f}")
