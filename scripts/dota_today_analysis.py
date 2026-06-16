"""Analyze TODAY's Dota bot activity (2026-05-26).

What did the bot SEE, DETECT, and TRADE today?

Sources:
  raw_snapshots.csv  - matches actually observed
  dota_events.csv    - event signals fired
  live_attempts.csv  - real CLOB orders attempted
  live_exits.csv     - exit orders + outcomes
  book_events.csv    - book activity (already in lol_analyze.py)
"""
from __future__ import annotations
import csv
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean

ROOT = Path('/home/tstuv/dota-poly-signal-pnl-asd')
TODAY = "2026-05-26"


def fnum(s):
    try: return float(s) if s not in (None, "") else None
    except: return None


# ----- Matches observed -----
print("="*70)
print(f"DOTA BOT TODAY ({TODAY}) — Matches observed")
print("="*70)
matches: dict[str, dict] = {}
with (ROOT/"logs/raw_snapshots.csv").open() as f:
    for row in csv.DictReader(f):
        if not row["received_at_utc"].startswith(TODAY): continue
        mid = row["match_id"]
        if mid not in matches:
            matches[mid] = {
                "first_seen": row["received_at_utc"],
                "league_id": row["league_id"],
                "snapshots": 0, "max_game_time": 0, "max_abs_lead": 0,
                "game_over": False, "max_score": 0,
            }
        m = matches[mid]
        m["snapshots"] += 1
        gt = int(float(row.get("game_time_sec") or 0)); m["max_game_time"] = max(m["max_game_time"], gt)
        rl = int(float(row.get("radiant_lead") or 0))
        if abs(rl) > abs(m["max_abs_lead"]): m["max_abs_lead"] = rl
        rs = int(float(row.get("radiant_score") or 0)); ds = int(float(row.get("dire_score") or 0))
        m["max_score"] = max(m["max_score"], rs + ds)
        if str(row.get("game_over", "")).lower() == "true": m["game_over"] = True
        m["last_seen"] = row["received_at_utc"]

print(f"Unique matches:       {len(matches)}")
print(f"Total snapshots:      {sum(m['snapshots'] for m in matches.values())}")
print(f"Reached game_over:    {sum(1 for m in matches.values() if m['game_over'])}")
print()
print(f"  {'match_id':>12s} {'snaps':>5s} {'maxGT':>6s} {'maxLead':>8s} {'totKills':>8s} {'GO':>2s}  {'first→last':<60s}")
for mid, m in sorted(matches.items(), key=lambda x: x[1]["first_seen"])[:30]:
    print(f"  {mid:>12s} {m['snapshots']:>5d} {m['max_game_time']/60:>6.1f}m "
          f"{m['max_abs_lead']:>+8d} {m['max_score']:>8d}  "
          f"{'✓' if m['game_over'] else '·':>2s}  "
          f"{m['first_seen'][11:19]} → {m['last_seen'][11:19]}")
if len(matches) > 30: print(f"  ... ({len(matches)-30} more)")
print()

# ----- Events fired -----
print("="*70)
print(f"EVENTS DETECTED TODAY")
print("="*70)
ev_counter = Counter()
ev_by_tier = Counter()
ev_match_unique = defaultdict(set)
with (ROOT/"logs/dota_events.csv").open() as f:
    for row in csv.DictReader(f):
        if not row["timestamp_utc"].startswith(TODAY): continue
        et = row["event_type"]; ev_counter[et] += 1
        ev_by_tier[row.get("event_tier", "?")] += 1
        ev_match_unique[et].add(row["match_id"])
total = sum(ev_counter.values())
print(f"Total events fired: {total}")
print(f"By tier: {dict(ev_by_tier)}\n")
print(f"  {'event':<35s}  {'n':>4s}  {'unique_matches':>14s}")
for ev, n in ev_counter.most_common():
    print(f"  {ev:<35s}  {n:>4d}  {len(ev_match_unique[ev]):>14d}")
print()

# ----- Live trades -----
print("="*70)
print(f"LIVE TRADES ATTEMPTED TODAY")
print("="*70)
attempts = []
with (ROOT/"logs/live_attempts.csv").open() as f:
    for row in csv.DictReader(f):
        if not row.get("created_at_utc","").startswith(TODAY) and \
           not row.get("timestamp_utc","").startswith(TODAY): continue
        attempts.append(row)
print(f"Live attempts today: {len(attempts)}")
for a in attempts[:20]:
    ts = (a.get("created_at_utc") or a.get("timestamp_utc",""))[:19]
    et = a.get("event_type", "?")
    status = a.get("order_status", "?")
    reason = a.get("reason_if_rejected") or a.get("reject_reason") or ""
    side = a.get("side", "?")
    price = a.get("price_cap") or a.get("price")
    size = a.get("submitted_size_usd") or a.get("filled_size_usd")
    name = (a.get("market_name") or "")[:50]
    print(f"  {ts}  {et:<28s}  {side:>3} ${size or '?':>4} @ {price or '?':>5}  {status:<12s} {reason[:30]}  {name}")
print()

# ----- Live exits -----
print("="*70)
print(f"LIVE EXITS TODAY")
print("="*70)
exits = []
exit_pnls = []
with (ROOT/"logs/live_exits.csv").open() as f:
    reader = csv.DictReader(f)
    for row in reader:
        ts = row.get("timestamp_utc","")
        if not ts.startswith(TODAY): continue
        exits.append(row)
        pnl = fnum(row.get("realized_pnl_usd") or row.get("pnl_usd"))
        if pnl is not None: exit_pnls.append(pnl)

# Group exits by reason
reason_counter = Counter()
heartbeat_count = 0
for ex in exits:
    r = ex.get("reason", "?")
    reason_counter[r] += 1
    if r == "startup_heartbeat": heartbeat_count += 1

print(f"Total exit rows: {len(exits)} ({heartbeat_count} startup heartbeats — bot restarts)")
print(f"Real exits:      {len(exits) - heartbeat_count}\n")
print(f"  reason                            n")
for r, n in reason_counter.most_common():
    print(f"  {r:<32s}  {n:>3d}")

if exit_pnls:
    total_pnl = sum(exit_pnls)
    print(f"\n  Realized P&L today (sum):  ${total_pnl:+.2f}")
    print(f"  Per-trade range:           ${min(exit_pnls):+.2f} to ${max(exit_pnls):+.2f}")

# Sample of recent exits with details
real_exits = [e for e in exits if e.get("reason") not in ("startup_heartbeat", None)]
if real_exits:
    print(f"\n  Sample of last 10 real exits:")
    for ex in real_exits[-10:]:
        ts = ex.get("timestamp_utc","")[:19]
        reason = ex.get("reason", "?")
        pid = ex.get("position_id", "?")[-12:]
        pnl = fnum(ex.get("realized_pnl_usd"))
        px = fnum(ex.get("price_posted") or ex.get("price"))
        shares = ex.get("shares_filled","")
        print(f"    {ts}  {reason:<28s}  pos={pid}  px={px}  shares={shares}  pnl={pnl}")
