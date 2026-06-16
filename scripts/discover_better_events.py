"""DISCOVER BETTER EVENT SIGNALS.

Mine the raw event + book data to find:
  1. Which existing dota_events have LARGE realized price moves the bot isn't capturing
  2. New event types we should add (e.g. BLOODY_EVEN_FIGHT, OBJECTIVE_CONVERSION_T2)
  3. Feature signatures (networth delta, kill delta, severity) that predict big moves
  4. Specific timing × event_type combos with REAL positive EV (not selection bias)

Method: for each event with a market mapping, compute the realized 60s/120s/settle
markout from book_events.csv. Rank events by EV * win_rate * sample_size.
"""
from __future__ import annotations

import csv
import yaml
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median, stdev

ROOT = Path('/home/tstuv/dota-poly-signal-pnl-asd')


def parse_ts(s: str) -> int:
    """ISO → ms epoch."""
    try: return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except: return 0


def fnum(s):
    try: return float(s) if s not in ("", None) else None
    except: return None


# ---------- LOAD ----------
print("Loading markets...")
markets = {}
data = yaml.safe_load(open(ROOT / "markets.yaml")) or {}
for m in data.get("markets", []):
    mid = str(m.get("dota_match_id") or "")
    if not mid or mid.startswith("STEAM_MATCH"): continue
    yes_team = (m.get("yes_team") or "").lower()
    radiant_team = (m.get("steam_radiant_team") or "").lower()
    if not yes_team: continue
    # Pick MAP_WINNER preferred
    if mid in markets and markets[mid].get("market_type") == "MAP_WINNER": continue
    markets[mid] = {
        "yes_tok": str(m.get("yes_token_id") or ""),
        "no_tok": str(m.get("no_token_id") or ""),
        "yes_team": yes_team,
        "radiant_team": radiant_team,
        "yes_is_radiant": yes_team == radiant_team,
    }
print(f"  {len(markets)} markets")

print("Loading book ticks (indexing by asset)...")
book_by_asset = defaultdict(list)  # asset_id -> [(ts_ms, mid_price)]
with (ROOT / "logs" / "book_events.csv").open() as f:
    for row in csv.DictReader(f):
        aid = row.get("asset_id", "")
        bid = fnum(row.get("best_bid"))
        ask = fnum(row.get("best_ask"))
        if bid is None or ask is None or aid == "": continue
        ts_ms = parse_ts(row["timestamp_utc"])
        if ts_ms == 0: continue
        mid = (bid + ask) / 2
        book_by_asset[aid].append((ts_ms, mid, bid, ask))
for k in book_by_asset:
    book_by_asset[k].sort()
print(f"  {len(book_by_asset)} assets, {sum(len(v) for v in book_by_asset.values())} ticks")

print("Loading events...")
events = []
with (ROOT / "logs" / "dota_events.csv").open() as f:
    for row in csv.DictReader(f):
        events.append(row)
print(f"  {len(events)} raw events\n")


# ---------- HELPERS ----------
def price_at(asset_id, target_ts_ms, side="mid"):
    """Latest tick at or before target_ts_ms within 30s."""
    ticks = book_by_asset.get(asset_id, [])
    if not ticks: return None
    # Linear search backward — small dataset OK
    px = None
    for ts, mid, bid, ask in ticks:
        if ts > target_ts_ms: break
        if side == "mid": px = mid
        elif side == "bid": px = bid
        elif side == "ask": px = ask
    return px


def signed_for_yes(event_row, raw_value):
    """A 'radiant-favorable' event needs sign flip if YES is dire."""
    mkt = markets.get(event_row["match_id"])
    if not mkt: return None
    direction = (event_row.get("direction") or "").lower()
    # direction may be 'radiant' / 'dire' / '' / 'radiant_favor' / 'dire_favor'
    if not direction: return raw_value  # use as-is (assume already signed)
    is_radiant_favor = "radiant" in direction
    if mkt["yes_is_radiant"] == is_radiant_favor: return raw_value
    return -raw_value


# ---------- ENRICH: realized markouts per event ----------
print("Computing realized markouts for each event...")
enriched = []
for ev in events:
    mid = ev["match_id"]
    mkt = markets.get(mid)
    if not mkt: continue
    ts_ev = parse_ts(ev["timestamp_utc"])
    if ts_ev == 0: continue
    yes_ask_0 = price_at(mkt["yes_tok"], ts_ev, "ask")
    yes_mid_0 = price_at(mkt["yes_tok"], ts_ev, "mid")
    if yes_ask_0 is None or yes_mid_0 is None: continue
    yes_60 = price_at(mkt["yes_tok"], ts_ev + 60_000, "mid")
    yes_120 = price_at(mkt["yes_tok"], ts_ev + 120_000, "mid")
    if yes_60 is None: continue

    # Realized markout (signed for the event direction)
    raw_60 = yes_60 - yes_ask_0
    raw_120 = (yes_120 - yes_ask_0) if yes_120 is not None else None
    signed_60 = signed_for_yes(ev, raw_60)
    signed_120 = signed_for_yes(ev, raw_120) if raw_120 is not None else None

    enriched.append({
        "event": ev["event_type"],
        "tier": ev.get("event_tier", ""),
        "family": ev.get("event_family", ""),
        "is_primary": ev.get("event_is_primary", "False"),
        "yes_ask_0": yes_ask_0,
        "yes_mid_0": yes_mid_0,
        "raw_60": raw_60,
        "raw_120": raw_120,
        "signed_60": signed_60,
        "signed_120": signed_120,
        "networth_delta": fnum(ev.get("networth_delta")) or 0,
        "kill_diff_delta": fnum(ev.get("kill_diff_delta")) or 0,
        "severity": fnum(ev.get("severity")) or 0,
        "base_pressure": fnum(ev.get("base_pressure_score")) or 0,
        "fight_pressure": fnum(ev.get("fight_pressure_score")) or 0,
        "economic_pressure": fnum(ev.get("economic_pressure_score")) or 0,
        "conversion": fnum(ev.get("conversion_score")) or 0,
        "confidence": fnum(ev.get("event_confidence")) or 0,
        "game_time": fnum(ev.get("game_time_sec")) or 0,
        "tier_short": ev.get("event_tier", "?"),
        "direction": ev.get("direction") or "",
    })

print(f"  {len(enriched)} events with valid book data")


# ---------- 1) RANK ALL EVENT TYPES BY REALIZED 60s EDGE ----------
print("\n" + "="*80)
print("RANK 1: All event types — realized 60s edge (DIRECTION-SIGNED)")
print("="*80)
print("Signed_60: positive = price moved in the direction the event predicted.")
print()

by_ev = defaultdict(list)
for r in enriched:
    by_ev[r["event"]].append(r)

print(f"  {'event':30s} {'n':>4s} {'signed_60_avg':>14s} {'med':>7s} {'win%':>5s} {'unsigned_avg':>13s} {'tier':>5s}")
ranked = sorted(by_ev.items(), key=lambda x: -mean([r["signed_60"] for r in x[1]]))
for ev, rs in ranked:
    if len(rs) < 3: continue
    signed = [r["signed_60"] for r in rs]
    unsigned = [abs(r["raw_60"]) for r in rs]
    w = sum(1 for v in signed if v > 0)
    tier = rs[0]["tier_short"]
    print(f"  {ev:30s} {len(rs):>4} {mean(signed):+12.4f} {median(signed):+7.4f}  {w/len(rs)*100:>4.0f}% {mean(unsigned):>+13.4f}  {tier:>5s}")


# ---------- 2) BOT'S CURRENT TRADED vs UNTRADED ----------
TRADED = {"POLL_FIGHT_SWING", "POLL_LATE_FIGHT_FLIP", "POLL_VALUE_DISAGREEMENT",
          "POLL_STRUCTURAL_DOMINANCE", "POLL_RAPID_STOMP", "POLL_DECISIVE_STOMP",
          "OBJECTIVE_CONVERSION_T2", "POLL_AEGIS_MOMENTUM"}

print("\n" + "="*80)
print("RANK 2: UNTRADED event types — bot is currently ignoring these")
print("="*80)
for ev, rs in ranked:
    if ev in TRADED: continue
    if len(rs) < 3: continue
    signed = [r["signed_60"] for r in rs]
    w = sum(1 for v in signed if v > 0)
    print(f"  UNTRADED {ev:30s} n={len(rs):>3}  avg_signed={mean(signed):+.4f}  win={w/len(rs)*100:>3.0f}%  family={rs[0]['family']}")


# ---------- 3) FEATURE PREDICTIVENESS ----------
print("\n" + "="*80)
print("RANK 3: Feature → outcome correlation (which model scores actually predict)")
print("="*80)

def corr(xs, ys):
    n = len(xs)
    mx, my = mean(xs), mean(ys)
    cov = sum((xs[i]-mx)*(ys[i]-my) for i in range(n)) / n
    sx, sy = stdev(xs), stdev(ys)
    if sx == 0 or sy == 0: return 0
    return cov / (sx * sy)

ys = [r["signed_60"] for r in enriched]
for feat in ["networth_delta", "kill_diff_delta", "severity", "base_pressure",
             "fight_pressure", "economic_pressure", "conversion", "confidence"]:
    xs = [r[feat] for r in enriched]
    c = corr(xs, ys)
    print(f"  corr({feat:25s}, signed_60) = {c:+.4f}")


# ---------- 4) HIGH-FEATURE buckets ----------
print("\n" + "="*80)
print("RANK 4: When networth_delta >= X, does signed_60 actually rise?")
print("="*80)
for thr in [0, 2000, 5000, 8000, 12000]:
    rs = [r for r in enriched if abs(r["networth_delta"]) >= thr]
    if not rs: continue
    signed = [r["signed_60"] for r in rs]
    w = sum(1 for v in signed if v > 0)
    print(f"  |nw_delta| >= {thr:>5}: n={len(rs):>3}  avg_signed={mean(signed):+.4f}  win={w/len(rs)*100:>3.0f}%  best={max(signed):+.3f}  worst={min(signed):+.3f}")

print()
for thr in [0, 1, 2, 3, 5]:
    rs = [r for r in enriched if abs(r["kill_diff_delta"]) >= thr]
    if not rs: continue
    signed = [r["signed_60"] for r in rs]
    w = sum(1 for v in signed if v > 0)
    print(f"  |kill_delta| >= {thr}: n={len(rs):>3}  avg_signed={mean(signed):+.4f}  win={w/len(rs)*100:>3.0f}%")

print()
for thr in [0, 0.3, 0.5, 0.7, 0.9]:
    rs = [r for r in enriched if r["severity"] >= thr]
    if not rs: continue
    signed = [r["signed_60"] for r in rs]
    w = sum(1 for v in signed if v > 0)
    print(f"  severity >= {thr}: n={len(rs):>3}  avg_signed={mean(signed):+.4f}  win={w/len(rs)*100:>3.0f}%")


# ---------- 5) COMBINED SIGNATURES (event + feature thresholds) ----------
print("\n" + "="*80)
print("RANK 5: Event × feature combos with best EV")
print("="*80)
print("Find: for each event, what feature threshold maximizes EV?")
combos = []
for ev in by_ev:
    rs = by_ev[ev]
    if len(rs) < 5: continue
    # Try: high-severity subset, high-nw-delta subset, high-kill-delta subset
    for feat, thr_options in [
        ("severity", [0.3, 0.5, 0.7]),
        ("networth_delta_abs", [2000, 5000, 8000]),
        ("kill_diff_delta_abs", [1, 2, 3]),
        ("confidence", [0.5, 0.7, 0.9]),
    ]:
        for thr in thr_options:
            if "_abs" in feat:
                key = feat.replace("_abs", "")
                sub = [r for r in rs if abs(r[key]) >= thr]
            else:
                sub = [r for r in rs if r[feat] >= thr]
            if len(sub) < 3: continue
            signed = [r["signed_60"] for r in sub]
            w = sum(1 for v in signed if v > 0)
            avg = mean(signed)
            combos.append({
                "event": ev, "filter": f"{feat}>={thr}", "n": len(sub),
                "avg": avg, "win_rate": w/len(sub),
                "score": avg * (len(sub)**0.5),
            })

combos.sort(key=lambda x: -x["score"])
print(f"\n  {'event':30s} {'filter':>25s}  {'n':>3s}  {'avg_signed':>11s}  {'win%':>5s}  {'score':>7s}")
for c in combos[:25]:
    if c["avg"] < 0.005: continue
    print(f"  {c['event']:30s} {c['filter']:>25s}  {c['n']:>3}  {c['avg']:+11.4f}  {c['win_rate']*100:>4.0f}%  {c['score']:+7.4f}")


# ---------- 6) BIG-PRICE-MOVE REVERSE ENGINEERING ----------
print("\n" + "="*80)
print("RANK 6: When YES_60 moves more than 5¢, what event fired?")
print("="*80)
big_moves = [r for r in enriched if abs(r["raw_60"]) >= 0.05]
print(f"  Total big-move events: {len(big_moves)}")
move_by_ev = defaultdict(list)
for r in big_moves:
    move_by_ev[r["event"]].append(r["signed_60"])
print()
for ev, vs in sorted(move_by_ev.items(), key=lambda x: -len(x[1])):
    w = sum(1 for v in vs if v > 0)
    print(f"  {ev:30s}  n={len(vs):>3}  avg_signed={mean(vs):+.4f}  win={w/len(vs)*100:>3.0f}%  median_size={median([abs(v) for v in vs]):.3f}")
