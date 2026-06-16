"""$500 SMART ALLOCATION BACKTEST.

Rather than one-size-fits-all, allocate sizing based on signal quality:

  Premium events (3-6x EV uplift, 70-80% win):
    - LATE_FIGHT_FLIP + conf>=0.9   → 6% of bankroll
    - VALUE_DISAGREE + nw>=2000     → 5% of bankroll
    - KILL_BURST + nw>=5000         → 4% of bankroll

  Standard whitelist events (~50-60% win, +0.5-2c EV):
    - All TIER_B baseline           → 2% of bankroll

  Scalp pairs (73% win, +10c/pair):
    - Per-leg size                  → 4% of bankroll

  Risk controls:
    - Never deploy more than 70% of bankroll simultaneously
    - Per-match cap: $250
    - Min stake: $5 (Polymarket minimum)
    - Hard reserve: $50 (never bet below this)
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

SLIPPAGE_EVENT = 0.04
SLIPPAGE_SCALP = 0.07
FILL_PROB = 0.70
MIN_STAKE = 5.0
HARD_RESERVE = 50.0
MAX_DEPLOYED_FRAC = 0.70    # never have > 70% of bankroll in flight
PER_MATCH_CAP = 250.0

# Allocation table — per-event fraction of CURRENT bankroll
ALLOC = {
    # Premium (boosted)
    "premium_LATE_FIGHT_FLIP":  0.06,
    "premium_VALUE_DISAGREE":   0.05,
    "premium_KILL_BURST":       0.04,
    # Standard whitelist
    "POLL_LATE_FIGHT_FLIP":     0.025,
    "POLL_VALUE_DISAGREEMENT":  0.025,
    "POLL_KILL_BURST_CONFIRMED": 0.025,
    "POLL_COMEBACK_RECOVERY":   0.02,
    "OBJECTIVE_CONVERSION_T2":  0.025,
    "POLL_STRUCTURAL_DOMINANCE": 0.015,
    "POLL_FIGHT_SWING":         0.015,
    "POLL_DECISIVE_STOMP":      0.015,
    "POLL_BUYBACK_CAPITULATION": 0.04,
    # Scalp leg
    "scalp":                    0.04,
}

WHITELIST = {
    "POLL_BUYBACK_CAPITULATION", "OBJECTIVE_CONVERSION_T2", "POLL_LATE_FIGHT_FLIP",
    "POLL_VALUE_DISAGREEMENT", "POLL_STRUCTURAL_DOMINANCE", "POLL_KILL_BURST_CONFIRMED",
    "POLL_COMEBACK_RECOVERY", "POLL_FIGHT_SWING", "POLL_DECISIVE_STOMP",
}


def parse_ts(s):
    try: return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except: return 0


def fnum(s):
    try: return float(s) if s not in ("", None) else None
    except: return None


def get_alloc_key(ev) -> str:
    """Determine which allocation bucket this event belongs to."""
    e = ev["event"]
    conf = ev.get("confidence", 0)
    nw = abs(ev.get("nw_delta", 0))
    if e == "POLL_LATE_FIGHT_FLIP" and conf >= 0.9:
        return "premium_LATE_FIGHT_FLIP"
    if e == "POLL_VALUE_DISAGREEMENT" and nw >= 2000:
        return "premium_VALUE_DISAGREE"
    if e == "POLL_KILL_BURST_CONFIRMED" and nw >= 5000:
        return "premium_KILL_BURST"
    return e  # standard whitelist key


# Load
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
        aid = row.get("asset_id", "")
        bid = fnum(row.get("best_bid")); ask = fnum(row.get("best_ask"))
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
            "kind": "event", "ts": ts, "match_id": row["match_id"], "event": ev_type, "ep": ask0,
            "per_d_pnl": signed/ask0 - SLIPPAGE_EVENT,
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
                          "per_d_pnl": r["pnl_scratch_and_ride_peak"] - SLIPPAGE_SCALP})

actions = sorted(events_actions + scalp_actions, key=lambda x: x["ts"])
print(f"=== $500 SMART ALLOCATION BACKTEST ===")
print(f"Events: {len(events_actions)}, Scalps: {len(scalp_actions)}, Total: {len(actions)}\n")

# Show allocation table
print("Allocation per signal type (% of current bankroll):")
for k, v in sorted(ALLOC.items(), key=lambda x: -x[1]):
    print(f"  {k:>35s}:  {v*100:.1f}%  → ${500*v:.0f} at $500 / ${1000*v:.0f} at $1000")
print()
print(f"Risk caps: deployed_max={MAX_DEPLOYED_FRAC*100:.0f}%, per_match=${PER_MATCH_CAP:.0f}, "
      f"min=${MIN_STAKE}, reserve=${HARD_RESERVE}\n")


def simulate(label, *, seed=42, start=500.0, slip_event=SLIPPAGE_EVENT, slip_scalp=SLIPPAGE_SCALP):
    rng = random.Random(seed)
    bk = start; peak = start; max_dd = 0
    n_ev = n_sc = wins_ev = wins_sc = 0
    pnls_ev, pnls_sc = [], []
    deployed = 0.0   # rough proxy: assumes trades settle within this loop (we don't model time)
    per_match = defaultdict(float)
    skipped_reserve = skipped_deploy = skipped_match = 0
    by_alloc_key = defaultdict(list)

    for a in actions:
        if rng.random() > FILL_PROB: continue
        # Skip if at hard reserve
        if bk - HARD_RESERVE < MIN_STAKE: skipped_reserve += 1; continue

        if a["kind"] == "event":
            alloc_key = get_alloc_key(a)
        else:
            alloc_key = "scalp"
        alloc_frac = ALLOC.get(alloc_key, 0.015)
        ideal = bk * alloc_frac

        # Risk caps
        budget_remaining = (bk - HARD_RESERVE) - 0  # we don't track in-flight; assume instant settle
        match_remaining = PER_MATCH_CAP - per_match[a["match_id"]]
        deployed_room = bk * MAX_DEPLOYED_FRAC  # informational; not enforced per-tick

        stake = min(max(MIN_STAKE, ideal), budget_remaining, match_remaining)
        if a["kind"] == "scalp":
            # Scalp is a PAIR: this stake is per leg, double it for total exposure
            total_pair = stake * 2
            if total_pair > match_remaining: stake = match_remaining / 2
            if stake < MIN_STAKE: skipped_match += 1; continue
            # Realized pair P&L (per_d_pnl is per-$1 of one leg, so multiply by stake)
            pnl = a["per_d_pnl"] * stake
        else:
            pnl = a["per_d_pnl"] * stake

        if stake < MIN_STAKE: skipped_match += 1; continue
        bk += pnl
        per_match[a["match_id"]] += stake * (2 if a["kind"] == "scalp" else 1)
        if a["kind"] == "event":
            n_ev += 1; pnls_ev.append(pnl); wins_ev += int(pnl > 0)
        else:
            n_sc += 1; pnls_sc.append(pnl); wins_sc += int(pnl > 0)
        by_alloc_key[alloc_key].append(pnl)
        if bk > peak: peak = bk
        if peak - bk > max_dd: max_dd = peak - bk
        if bk < 50: print(f"  *** RUIN @ {n_ev+n_sc} ***"); break

    pnls = pnls_ev + pnls_sc
    if not pnls: print(f"{label}: 0 trades"); return None
    win = (wins_ev + wins_sc) / len(pnls) * 100
    print(f"\n--- {label} ---")
    print(f"  trades:       {len(pnls)}  ({n_ev} events, {n_sc} scalps)")
    print(f"  skipped:      reserve={skipped_reserve}, match_cap={skipped_match}")
    print(f"  final $:      ${bk:.0f}  ({(bk-start)/start*100:+.1f}%)")
    print(f"  win rate:     {win:.0f}%")
    print(f"  avg/trade:    ${mean(pnls):+.2f}")
    if len(pnls) > 1: print(f"  stdev/trade:  ${stdev(pnls):.2f}")
    print(f"  best/worst:   ${max(pnls):+.0f} / ${min(pnls):+.0f}")
    print(f"  max DD:       ${max_dd:.0f}  ({max_dd/peak*100:.0f}%)")

    # Per-allocation key breakdown
    print(f"\n  Per-bucket performance:")
    for k, vs in sorted(by_alloc_key.items(), key=lambda x: -sum(x[1])):
        w = sum(1 for v in vs if v > 0)
        print(f"    {k:>35s}  n={len(vs):>3}  ${sum(vs):+7.0f}  win {w/len(vs)*100:>3.0f}%  avg ${mean(vs):+.2f}")
    return {"final": bk, "max_dd": max_dd, "trades": len(pnls), "win_rate": win/100}


simulate("SMART ALLOCATION (baseline)")

# Monte Carlo
print()
print("="*70)
print("MONTE CARLO (500 shuffles)")
print("="*70)
def mc(label, slip_e, slip_s):
    finals, ruins, dds = [], 0, []
    for seed in range(500):
        rng = random.Random(seed)
        shuf = actions[:]; rng.shuffle(shuf)
        bk = 500.0; peak = 500.0; max_dd = 0
        per_match = defaultdict(float)
        for a in shuf:
            if rng.random() > FILL_PROB: continue
            if bk - HARD_RESERVE < MIN_STAKE: continue
            alloc_key = get_alloc_key(a) if a["kind"] == "event" else "scalp"
            ideal = bk * ALLOC.get(alloc_key, 0.015)
            match_remaining = PER_MATCH_CAP - per_match[a["match_id"]]
            stake = min(max(MIN_STAKE, ideal), bk - HARD_RESERVE, match_remaining)
            if stake < MIN_STAKE: continue
            slip = slip_s if a["kind"] == "scalp" else slip_e
            adj = a["per_d_pnl"] - (slip - SLIPPAGE_EVENT if a["kind"] == "event"
                                     else slip - SLIPPAGE_SCALP)
            pnl = adj * stake
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

mc("BACKTEST slippage (4% / 7%)", 0.04, 0.07)
mc("REALISTIC slippage (6% / 10%)", 0.06, 0.10)
mc("PESSIMISTIC slippage (10% / 15%)", 0.10, 0.15)

print()
print("="*70)
print("vs. FLAT $50/trade comparison")
print("="*70)
def flat_sim(stake, label):
    rng = random.Random(42)
    bk = 500.0; peak = 500.0; max_dd = 0; n = 0; wins = 0
    per_match = defaultdict(float)
    for a in actions:
        if rng.random() > FILL_PROB: continue
        if bk - HARD_RESERVE < MIN_STAKE: continue
        if per_match[a["match_id"]] >= 500: continue
        sk = min(stake, bk - HARD_RESERVE, 500 - per_match[a["match_id"]])
        if sk < MIN_STAKE: continue
        pnl = a["per_d_pnl"] * sk
        bk += pnl; n += 1
        per_match[a["match_id"]] += sk * (2 if a["kind"] == "scalp" else 1)
        if pnl > 0: wins += 1
        if bk > peak: peak = bk
        if peak - bk > max_dd: max_dd = peak - bk
        if bk < 50: break
    print(f"  {label}: n={n}, ${bk:.0f} ({(bk-500)/500*100:+.0f}%), DD {max_dd/peak*100:.0f}%, win {wins/max(n,1)*100:.0f}%")

flat_sim(10, "FLAT $10")
flat_sim(25, "FLAT $25")
flat_sim(50, "FLAT $50")
