"""Backtest the IMPROVED event whitelist on full 1059-event dataset.

Whitelist (from discover_better_events.py):
  TIER_A_PREMIUM:  POLL_LATE_FIGHT_FLIP + confidence>=0.9
                   POLL_VALUE_DISAGREEMENT + nw_delta>=2000
                   POLL_KILL_BURST_CONFIRMED + nw_delta>=5000
  TIER_A:          POLL_LATE_FIGHT_FLIP, POLL_VALUE_DISAGREEMENT,
                   POLL_KILL_BURST_CONFIRMED, POLL_COMEBACK_RECOVERY,
                   OBJECTIVE_CONVERSION_T2, POLL_STRUCTURAL_DOMINANCE

Realistic assumptions:
  - Slippage: 0.04 round-trip
  - Fill rate: 70% (some events fire when book is wide/stale)
  - Position cap: $50 (Polymarket liquidity ceiling)
  - Exit: 60s markout (proxy for realistic exit)
  - $500 bankroll
"""
from __future__ import annotations
import csv, random, yaml
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, stdev

ROOT = Path('/home/tstuv/dota-poly-signal-pnl-asd')

SLIPPAGE = 0.04
FILL_PROB = 0.70
POS_CAP = 50.0

TIER_A = {
    "POLL_LATE_FIGHT_FLIP", "POLL_VALUE_DISAGREEMENT",
    "POLL_KILL_BURST_CONFIRMED", "POLL_COMEBACK_RECOVERY",
    "OBJECTIVE_CONVERSION_T2", "POLL_STRUCTURAL_DOMINANCE",
}
TIER_B = {"POLL_FIGHT_SWING", "POLL_DECISIVE_STOMP"}
BLACKLIST = {
    "POLL_STOMP_THROW_CONFIRMED", "POLL_LEAD_FLIP_WITH_KILLS",
    "POLL_ULTRA_LATE_FIGHT_FLIP", "POLL_RAPID_STOMP",
    "BLOODY_EVEN_FIGHT", "POLL_MAJOR_COMEBACK_RECOVERY",
}


def is_premium(ev):
    if ev["event"] == "POLL_LATE_FIGHT_FLIP" and ev["confidence"] >= 0.9: return True
    if ev["event"] == "POLL_VALUE_DISAGREEMENT" and abs(ev["nw_delta"]) >= 2000: return True
    if ev["event"] == "POLL_KILL_BURST_CONFIRMED" and abs(ev["nw_delta"]) >= 5000: return True
    return False


def parse_ts(s):
    try: return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except: return 0


def fnum(s):
    try: return float(s) if s not in ("", None) else None
    except: return None


# Load
markets = {}
for m in (yaml.safe_load(open(ROOT/"markets.yaml")) or {}).get("markets", []):
    mid = str(m.get("dota_match_id") or "")
    if not mid or mid.startswith("STEAM_MATCH"): continue
    yes = (m.get("yes_team") or "").lower()
    rad = (m.get("steam_radiant_team") or "").lower()
    if not yes: continue
    if mid in markets and markets[mid]["mtype"] == "MAP_WINNER": continue
    markets[mid] = {"yes_tok": str(m.get("yes_token_id") or ""),
                    "yes_is_radiant": yes == rad,
                    "mtype": m.get("market_type", "")}

book = defaultdict(list)
with (ROOT/"logs/book_events.csv").open() as f:
    for row in csv.DictReader(f):
        aid = row.get("asset_id", "")
        bid = fnum(row.get("best_bid")); ask = fnum(row.get("best_ask"))
        if None in (bid, ask) or not aid: continue
        ts = parse_ts(row["timestamp_utc"])
        if ts: book[aid].append((ts, (bid+ask)/2, bid, ask))
for k in book: book[k].sort()

def price_at(aid, ts, side="mid"):
    ticks = book.get(aid, [])
    px = None
    for t, m, b, a in ticks:
        if t > ts: break
        px = {"mid": m, "bid": b, "ask": a}[side]
    return px

enriched = []
with (ROOT/"logs/dota_events.csv").open() as f:
    for row in csv.DictReader(f):
        mkt = markets.get(row["match_id"])
        if not mkt: continue
        ts = parse_ts(row["timestamp_utc"])
        if not ts: continue
        ask0 = price_at(mkt["yes_tok"], ts, "ask")
        m60 = price_at(mkt["yes_tok"], ts+60_000, "mid")
        if None in (ask0, m60): continue
        raw_60 = m60 - ask0
        direction = (row.get("direction") or "").lower()
        is_rad = "radiant" in direction
        # If no direction, infer from networth_delta sign (positive=radiant favor)
        nw = fnum(row.get("networth_delta")) or 0
        if not direction:
            is_rad = nw > 0
        signed = raw_60 if mkt["yes_is_radiant"] == is_rad else -raw_60
        enriched.append({
            "event": row["event_type"], "ts": ts,
            "ep": ask0, "raw_60": raw_60, "signed_60": signed,
            "nw_delta": nw, "kill_delta": fnum(row.get("kill_diff_delta")) or 0,
            "confidence": fnum(row.get("event_confidence")) or 0,
            "match_id": row["match_id"],
        })

enriched.sort(key=lambda e: e["ts"])
print(f"=== IMPROVED WHITELIST BACKTEST ===")
print(f"{len(enriched)} events with valid book data, slippage {SLIPPAGE*100:.0f}%, fill {FILL_PROB*100:.0f}%, cap ${POS_CAP:.0f}\n")


def per_d(ev, premium_mult=1.0):
    """Per-$ PnL after slippage. Premium gets weighted higher in sizing."""
    return ev["signed_60"] / ev["ep"] - SLIPPAGE


def passes_tier_a(ev):
    if ev["event"] in BLACKLIST: return False
    return ev["event"] in TIER_A


def passes_tier_b(ev):
    return ev["event"] in TIER_B and ev["event"] not in BLACKLIST


def passes_premium(ev):
    return is_premium(ev)


def simulate(label, filter_fn, base_stake, *, premium_mult=2.0, frac=None, seed=42, start=500.0):
    rng = random.Random(seed)
    bk = start; peak = start; max_dd = 0
    n = 0; wins = 0; pnls = []
    skipped = nofill = 0
    cooldown = {}  # match_id -> last_ts_ms
    COOL_MS = 60_000
    for ev in enriched:
        if not filter_fn(ev): skipped += 1; continue
        last = cooldown.get(ev["match_id"], 0)
        if ev["ts"] - last < COOL_MS: skipped += 1; continue
        if rng.random() > FILL_PROB: nofill += 1; continue
        mult = premium_mult if is_premium(ev) else 1.0
        if frac is not None:
            ideal = bk * frac * mult
        else:
            ideal = base_stake * mult
        stake = min(max(5.0, ideal), POS_CAP)
        if stake > bk: continue
        pnl = per_d(ev) * stake
        bk += pnl; n += 1; pnls.append(pnl)
        cooldown[ev["match_id"]] = ev["ts"]
        if pnl > 0: wins += 1
        if bk > peak: peak = bk
        if peak - bk > max_dd: max_dd = peak - bk
        if bk < 50: print(f"  *** RUIN at trade {n} ***"); break
    if n == 0: print(f"{label}: 0 trades"); return
    print(f"\n--- {label} ---")
    print(f"  trades:        {n} filled, {nofill} no-fill, {skipped} filtered")
    print(f"  final $:       ${bk:.0f}  ({(bk-start)/start*100:+.1f}%)")
    print(f"  win rate:      {wins/n*100:.0f}%")
    print(f"  avg/trade:     ${mean(pnls):+.2f}")
    if n >= 2: print(f"  stdev/trade:   ${stdev(pnls):.2f}")
    print(f"  best/worst:    ${max(pnls):+.2f} / ${min(pnls):+.2f}")
    print(f"  max drawdown:  ${max_dd:.0f} ({max_dd/peak*100:.0f}%)")


# Scenarios
print("="*60)
print("SCENARIO A: TIER_A only (whitelist, no premium boost)")
print("="*60)
for s in [10, 25, 50]:
    simulate(f"TIER_A FIXED ${s}", passes_tier_a, s, premium_mult=1.0)
for f in [0.02, 0.05, 0.10]:
    simulate(f"TIER_A COMPOUND {f*100:.0f}%", passes_tier_a, 0, premium_mult=1.0, frac=f)

print("\n" + "="*60)
print("SCENARIO B: TIER_A + 2× boost on PREMIUM combos")
print("="*60)
for s in [10, 25, 50]:
    simulate(f"TIER_A+P ${s}", passes_tier_a, s, premium_mult=2.0)
for f in [0.02, 0.05, 0.10]:
    simulate(f"TIER_A+P {f*100:.0f}%", passes_tier_a, 0, premium_mult=2.0, frac=f)

print("\n" + "="*60)
print("SCENARIO C: PREMIUM ONLY (highest-confidence subset)")
print("="*60)
for s in [25, 50]:
    simulate(f"PREMIUM ONLY ${s}", passes_premium, s, premium_mult=1.0)

print("\n" + "="*60)
print("SCENARIO D: TIER_A + TIER_B (include marginal)")
print("="*60)
def passes_ab(ev):
    return (passes_tier_a(ev) or passes_tier_b(ev))
for s in [10, 25, 50]:
    simulate(f"A+B ${s}", passes_ab, s)

print("\n" + "="*60)
print("MONTE CARLO — TIER_A FIXED $25, 500 shuffles")
print("="*60)
def mc(stake, filter_fn, label):
    finals, ruins = [], 0
    for seed in range(500):
        rng = random.Random(seed)
        shuf = enriched[:]; rng.shuffle(shuf)
        bk = 500.0
        cooldown = {}
        for ev in shuf:
            if not filter_fn(ev): continue
            last = cooldown.get(ev["match_id"], 0)
            if ev["ts"] - last < 60_000: continue
            if rng.random() > FILL_PROB: continue
            stk = min(max(5.0, stake), POS_CAP)
            if stk > bk: continue
            bk += per_d(ev) * stk
            cooldown[ev["match_id"]] = ev["ts"]
            if bk < 50: ruins += 1; break
        finals.append(bk)
    finals.sort()
    print(f"  {label}: 5th=${finals[25]:.0f}  med=${finals[250]:.0f}  95th=${finals[475]:.0f}  ruin={ruins}/500")

for stake in [10, 25, 50]:
    mc(stake, passes_tier_a, f"TIER_A FIXED ${stake}")
for stake in [25, 50]:
    mc(stake, passes_premium, f"PREMIUM ONLY ${stake}")
