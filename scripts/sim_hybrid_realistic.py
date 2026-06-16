"""$500 HYBRID strategy backtest, REALISTIC assumptions.

Combines:
  - FLAT $50 sizing for PREMIUM events (proven high-EV, can absorb the bet)
  - SMART (% of bankroll) for standard events
  - $50/leg ($100/pair) for scalps
  - DROP the proven losers (POLL_STRUCTURAL_DOMINANCE, standalone VALUE_DISAGREE)

REALISTIC adjustments vs backtest:
  - Slippage 6% events / 10% scalp (vs 4%/7%)
  - Fill rate 60% (vs 70%) — book moves during placement
  - Premium feature thresholds met less often in live (haircut subset by 30%)
"""
from __future__ import annotations
import csv, random, sys, yaml
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, stdev

ROOT = Path('/home/tstuv/dota-poly-signal-pnl-asd')
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'scripts'))
from backtest_buy_both_scalp import _load_markets as _ls
from backtest_buy_both_scalp import _load_match_windows, simulate_one as sim_scalp

# REALISTIC defaults
SLIPPAGE_EVENT = 0.06
SLIPPAGE_SCALP = 0.10
FILL_PROB = 0.60               # was 0.70
PREMIUM_LIVE_HAIRCUT = 0.30    # 30% of premium signals fail live (features not preserved)
MIN_STAKE = 5.0
HARD_RESERVE = 50.0
PER_MATCH_CAP = 500.0
TOTAL_LIVE_CAP = 2000.0

PREMIUM_FLAT_USD = 100.0       # FLAT $100 on premium (uses bot's PREMIUM_SIZE_MULT * MAX_TRADE_USD)
SCALP_LEG_USD = 50.0           # $50 per leg

# Standard allocations — % of current bankroll. DROPPED the losers.
STANDARD_ALLOC = {
    "POLL_BUYBACK_CAPITULATION":  0.04,
    "POLL_LATE_FIGHT_FLIP":       0.025,
    "POLL_KILL_BURST_CONFIRMED":  0.025,
    "OBJECTIVE_CONVERSION_T2":    0.025,
    "POLL_FIGHT_SWING":           0.015,
    "POLL_COMEBACK_RECOVERY":     0.02,
    "POLL_DECISIVE_STOMP":        0.015,
    # POLL_STRUCTURAL_DOMINANCE: dropped (was -$38 in last backtest)
    # POLL_VALUE_DISAGREEMENT: standalone dropped (was -$10, kept only as premium)
}

DROP_STANDALONE = {"POLL_STRUCTURAL_DOMINANCE", "POLL_VALUE_DISAGREEMENT"}

WHITELIST = set(STANDARD_ALLOC.keys()) | DROP_STANDALONE | {"POLL_VALUE_DISAGREEMENT"}


def parse_ts(s):
    try: return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except: return 0


def fnum(s):
    try: return float(s) if s not in ("", None) else None
    except: return None


def is_premium(ev):
    e = ev["event"]; conf = ev.get("confidence", 0); nw = abs(ev.get("nw_delta", 0))
    if e == "POLL_LATE_FIGHT_FLIP" and conf >= 0.9: return True
    if e == "POLL_VALUE_DISAGREEMENT" and nw >= 2000: return True
    if e == "POLL_KILL_BURST_CONFIRMED" and nw >= 5000: return True
    return False


markets = {}
for m in (yaml.safe_load(open(ROOT/"markets.yaml")) or {}).get("markets", []):
    mid = str(m.get("dota_match_id") or "")
    if not mid or mid.startswith("STEAM_MATCH"): continue
    yes = (m.get("yes_team") or "").lower(); rad = (m.get("steam_radiant_team") or "").lower()
    if not yes: continue
    if mid in markets and markets[mid]["mtype"] == "MAP_WINNER": continue
    markets[mid] = {"yes_tok": str(m.get("yes_token_id") or ""), "yes_is_radiant": yes == rad,
                    "mtype": m.get("market_type", "")}

book = defaultdict(list)
with (ROOT/"logs/book_events.csv").open() as f:
    for row in csv.DictReader(f):
        aid = row.get("asset_id", ""); bid = fnum(row.get("best_bid")); ask = fnum(row.get("best_ask"))
        if None in (bid, ask) or not aid: continue
        ts = parse_ts(row["timestamp_utc"])
        if ts: book[aid].append((ts, (bid+ask)/2, bid, ask))
for k in book: book[k].sort()

def price_at(aid, ts):
    px = None
    for t, m, b, a in book.get(aid, []):
        if t > ts: break
        px = (m, b, a)
    return px

events_actions = []
with (ROOT/"logs/dota_events.csv").open() as f:
    for row in csv.DictReader(f):
        ev_type = row["event_type"]
        if ev_type not in WHITELIST: continue
        mkt = markets.get(row["match_id"])
        if not mkt: continue
        ts = parse_ts(row["timestamp_utc"])
        if not ts: continue
        p0 = price_at(mkt["yes_tok"], ts); p60 = price_at(mkt["yes_tok"], ts+60_000)
        if not p0 or not p60: continue
        ask0 = p0[2]; m60 = p60[0]
        if ask0 is None or m60 is None: continue
        nw = fnum(row.get("networth_delta")) or 0
        direction = (row.get("direction") or "").lower()
        is_rad = "radiant" in direction if direction else nw > 0
        raw_60 = m60 - ask0
        signed = raw_60 if mkt["yes_is_radiant"] == is_rad else -raw_60
        events_actions.append({
            "kind": "event", "ts": ts, "match_id": row["match_id"],
            "event": ev_type, "ep": ask0,
            "raw_signed_60": signed,
            "confidence": fnum(row.get("event_confidence")) or 0,
            "nw_delta": nw,
        })

scalp_markets = _ls(); scalp_windows = _load_match_windows()
scalp_actions = []
for mid, (t0, tN, rl) in scalp_windows.items():
    if mid not in scalp_markets: continue
    r = sim_scalp(mid, scalp_markets[mid], t0, tN, rl)
    if not r: continue
    skew = abs(r["yes_entry"] - r["no_entry"]); s_sum = r["yes_entry"] + r["no_entry"]
    if skew > 0.08 or s_sum > 1.03: continue
    scalp_actions.append({"kind": "scalp", "ts": t0, "match_id": mid,
                          "raw_pnl_per_d": r["pnl_scratch_and_ride_peak"]})

actions = sorted(events_actions + scalp_actions, key=lambda x: x["ts"])

print("=== $500 HYBRID + REALISTIC BACKTEST ===")
print(f"Whitelist size: {len(WHITELIST)} events, {len(DROP_STANDALONE)} standalone-dropped")
print(f"Events: {len(events_actions)}, Scalps: {len(scalp_actions)}, Total: {len(actions)}\n")
print(f"REALISTIC params:")
print(f"  Fill rate: {FILL_PROB*100:.0f}%  (vs backtest 70%)")
print(f"  Slippage events: {SLIPPAGE_EVENT*100:.0f}%  (vs backtest 4%)")
print(f"  Slippage scalp:  {SLIPPAGE_SCALP*100:.0f}%  (vs backtest 7%)")
print(f"  Premium feature haircut: {PREMIUM_LIVE_HAIRCUT*100:.0f}%  (rare in live)\n")


def get_stake(a, bankroll, rng):
    """Determine actual stake; returns 0 if should skip."""
    e = a["event"] if a["kind"] == "event" else "scalp"

    if a["kind"] == "scalp":
        return SCALP_LEG_USD  # per-leg; pair total = 2x

    if is_premium(a):
        # Haircut: 30% of premium signals get downgraded to standard in live
        if rng.random() < PREMIUM_LIVE_HAIRCUT:
            # Downgrade to standard allocation
            return bankroll * STANDARD_ALLOC.get(e, 0.015)
        return PREMIUM_FLAT_USD  # FLAT $100

    if e in DROP_STANDALONE and not is_premium(a):
        return 0  # standalone losers — skip

    return bankroll * STANDARD_ALLOC.get(e, 0.015)


def adjusted_pnl(a):
    """Apply realistic slippage to the raw per-$1 PnL."""
    if a["kind"] == "scalp":
        return a["raw_pnl_per_d"] - SLIPPAGE_SCALP
    return a["raw_signed_60"] / a["ep"] - SLIPPAGE_EVENT


def simulate(label, *, seed=42, start=500.0):
    rng = random.Random(seed)
    bk = start; peak = start; max_dd = 0
    n_ev = n_sc = wins_ev = wins_sc = 0
    pnls_ev, pnls_sc = [], []
    per_match = defaultdict(float)
    by_bucket = defaultdict(list)
    total_in_flight = 0.0  # simplistic, treats trades as instant settle

    skipped_fill = skipped_match = skipped_reserve = skipped_dropped = 0

    for a in actions:
        if rng.random() > FILL_PROB: skipped_fill += 1; continue
        if bk - HARD_RESERVE < MIN_STAKE: skipped_reserve += 1; continue
        if per_match[a["match_id"]] >= PER_MATCH_CAP: skipped_match += 1; continue

        stake_target = get_stake(a, bk, rng)
        if stake_target <= 0: skipped_dropped += 1; continue

        stake = min(max(MIN_STAKE, stake_target),
                    bk - HARD_RESERVE,
                    PER_MATCH_CAP - per_match[a["match_id"]])
        if a["kind"] == "scalp" and stake * 2 > (PER_MATCH_CAP - per_match[a["match_id"]]):
            stake = (PER_MATCH_CAP - per_match[a["match_id"]]) / 2
        if stake < MIN_STAKE: skipped_match += 1; continue

        pnl = adjusted_pnl(a) * stake
        bk += pnl
        per_match[a["match_id"]] += stake * (2 if a["kind"] == "scalp" else 1)

        # Bucket for reporting
        if a["kind"] == "scalp": bucket = "scalp"
        elif is_premium(a) and stake >= PREMIUM_FLAT_USD * 0.9: bucket = f"PREMIUM_{a['event']}"
        elif a["event"] in DROP_STANDALONE: bucket = f"premium_only_{a['event']}"  # came through as premium
        else: bucket = a["event"]
        by_bucket[bucket].append(pnl)

        if a["kind"] == "event":
            n_ev += 1; pnls_ev.append(pnl); wins_ev += int(pnl > 0)
        else:
            n_sc += 1; pnls_sc.append(pnl); wins_sc += int(pnl > 0)
        if bk > peak: peak = bk
        if peak - bk > max_dd: max_dd = peak - bk
        if bk < 50: print(f"  *** RUIN @ {n_ev+n_sc} ***"); break

    pnls = pnls_ev + pnls_sc
    if not pnls: print(f"{label}: 0 trades"); return None
    win = (wins_ev + wins_sc) / len(pnls) * 100
    print(f"--- {label} ---")
    print(f"  trades:       {len(pnls)}  ({n_ev} events, {n_sc} scalps)")
    print(f"  skipped:      fill={skipped_fill}, match_cap={skipped_match}, "
          f"reserve={skipped_reserve}, dropped={skipped_dropped}")
    print(f"  final $:      ${bk:.0f}  ({(bk-start)/start*100:+.1f}%)")
    print(f"  win rate:     {win:.0f}%")
    print(f"  avg/trade:    ${mean(pnls):+.2f}")
    if len(pnls) > 1: print(f"  stdev/trade:  ${stdev(pnls):.2f}")
    print(f"  best/worst:   ${max(pnls):+.0f} / ${min(pnls):+.0f}")
    print(f"  max DD:       ${max_dd:.0f}  ({max_dd/peak*100:.0f}%)")
    print(f"\n  Per-bucket P&L:")
    for k, vs in sorted(by_bucket.items(), key=lambda x: -sum(x[1])):
        w = sum(1 for v in vs if v > 0)
        print(f"    {k:>40s}  n={len(vs):>3}  ${sum(vs):+7.0f}  win {w/len(vs)*100:>3.0f}%  avg ${mean(vs):+.2f}")
    return bk


simulate("HYBRID + REALISTIC (baseline seed=42)")

print()
print("="*70)
print("MONTE CARLO (500 shuffles, REALISTIC params)")
print("="*70)
def mc(label, *, slip_e, slip_s, fill, haircut):
    finals, ruins, dds = [], 0, []
    for seed in range(500):
        rng = random.Random(seed)
        shuf = actions[:]; rng.shuffle(shuf)
        bk = 500.0; peak = 500.0; max_dd = 0
        per_match = defaultdict(float)
        for a in shuf:
            if rng.random() > fill: continue
            if bk - HARD_RESERVE < MIN_STAKE: continue
            if per_match[a["match_id"]] >= PER_MATCH_CAP: continue
            # Stake
            if a["kind"] == "scalp": stake = SCALP_LEG_USD
            elif is_premium(a):
                if rng.random() < haircut:
                    stake = bk * STANDARD_ALLOC.get(a["event"], 0.015)
                else: stake = PREMIUM_FLAT_USD
            elif a["event"] in DROP_STANDALONE: continue
            else: stake = bk * STANDARD_ALLOC.get(a["event"], 0.015)
            stake = min(max(MIN_STAKE, stake), bk - HARD_RESERVE,
                         PER_MATCH_CAP - per_match[a["match_id"]])
            if stake < MIN_STAKE: continue
            slip = slip_s if a["kind"] == "scalp" else slip_e
            raw = a["raw_pnl_per_d"] if a["kind"] == "scalp" else a["raw_signed_60"] / a["ep"]
            pnl = (raw - slip) * stake
            bk += pnl
            per_match[a["match_id"]] += stake * (2 if a["kind"] == "scalp" else 1)
            if bk > peak: peak = bk
            if peak - bk > max_dd: max_dd = peak - bk
            if bk < 50: ruins += 1; break
        finals.append(bk); dds.append(max_dd / max(peak, 1) * 100)
    finals.sort(); dds.sort()
    print(f"  {label}:")
    print(f"     final:  5th=${finals[25]:>5.0f}  med=${finals[250]:>5.0f}  95th=${finals[475]:>5.0f}")
    print(f"     maxDD:  5th={dds[25]:>4.0f}%  med={dds[250]:>4.0f}%  95th={dds[475]:>4.0f}%")
    print(f"     ruin:   {ruins}/500 ({ruins/5:.1f}%)")

mc("REALISTIC base   (6%/10%, fill 60%, haircut 30%)", slip_e=0.06, slip_s=0.10, fill=0.60, haircut=0.30)
mc("PESSIMISTIC      (8%/12%, fill 50%, haircut 50%)", slip_e=0.08, slip_s=0.12, fill=0.50, haircut=0.50)
mc("OPTIMISTIC backtest (4%/7%, fill 70%, haircut 10%)", slip_e=0.04, slip_s=0.07, fill=0.70, haircut=0.10)
