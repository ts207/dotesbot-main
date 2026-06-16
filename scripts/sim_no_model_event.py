"""NO-MODEL EVENT STRATEGY BACKTEST.

The model's executable_edge has correlation -0.04 with realized PnL.
That number is junk. So throw the model out entirely and trade on pure
structural rules drawn from the audit:

  GATES (any failure → skip):
    1. spread <= 0.04
    2. event ∈ whitelist (only events with proven positive EV)
    3. entry_price < 0.45 OR entry_price >= 0.70 (skip toss-up zone)
    4. game_time satisfies per-event timing rule

  EVENT WHITELIST + timing rules:
    POLL_RAPID_STOMP          @ game_time >= 2400s
    POLL_LATE_FIGHT_FLIP      @ game_time >= 2400s
    POLL_FIGHT_SWING          @ entry_price >= 0.70 (any timing)
    POLL_LEAD_FLIP_WITH_KILLS @ game_time in [600, 1500) (mid game only)
    POLL_STOMP_THROW_CONFIRMED @ game_time in [1500, 2400) (late only)

  SIZING: flat $X per trade. No edge weighting (because edge is noise).

  EXIT: 60s markout (proxy for realistic exit).

  SLIPPAGE: 0.04 round-trip (4% drag). 0.08 was for the bad-spread case;
  with spread <= 0.04 gate, real slippage is closer to 4%.
"""
from __future__ import annotations

import csv
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

ROOT = Path('/home/tstuv/dota-poly-signal-pnl-asd')

SLIPPAGE = 0.04
FILL_PROBABILITY = 0.85   # tight spreads = better fill rate
POSITION_CAP_DOLLARS = 50.0


WHITELIST = {
    "POLL_RAPID_STOMP":           lambda gt, p: gt >= 2400,
    "POLL_LATE_FIGHT_FLIP":       lambda gt, p: gt >= 2400,
    "POLL_FIGHT_SWING":           lambda gt, p: p >= 0.70,
    "POLL_LEAD_FLIP_WITH_KILLS":  lambda gt, p: 600 <= gt < 1500,
    "POLL_STOMP_THROW_CONFIRMED": lambda gt, p: 1500 <= gt < 2400,
}


def fnum(s):
    try: return float(s) if s not in ("", None) else None
    except: return None


def load_shadow_all_paper():
    out = []
    with (ROOT / "logs" / "shadow_trades.csv").open() as f:
        for row in csv.DictReader(f):
            if row.get("decision") != "paper_buy_yes":
                continue
            ep = fnum(row.get("entry_price")) or fnum(row.get("ask_at_entry"))
            sp = fnum(row.get("spread_at_entry"))
            gt = fnum(row.get("game_time_sec"))
            m60 = fnum(row.get("markout_60s"))
            if None in (ep, sp, gt, m60):
                continue
            out.append({
                "event": row.get("event_type", ""),
                "ep": ep, "sp": sp, "gt": gt, "m60": m60,
            })
    return out


def passes_gates(t) -> bool:
    if t["sp"] > 0.04: return False
    if 0.45 <= t["ep"] < 0.70: return False
    rule = WHITELIST.get(t["event"])
    if rule is None: return False
    return rule(t["gt"], t["ep"])


def per_d_pnl(t):
    return (t["m60"] / t["ep"]) - SLIPPAGE


def simulate(label, trades, *, stake_usd=None, frac=None, start=500.0, seed=42):
    bankroll = start
    peak = bankroll
    max_dd = 0.0
    n = 0; wins = 0; pnls = []
    gated = 0; nofill = 0
    rng = random.Random(seed)
    for t in trades:
        if not passes_gates(t):
            gated += 1; continue
        if rng.random() > FILL_PROBABILITY:
            nofill += 1; continue
        ideal = bankroll * frac if frac is not None else stake_usd
        stake = min(max(5.0, ideal), POSITION_CAP_DOLLARS)
        if stake > bankroll: continue
        pnl = per_d_pnl(t) * stake
        bankroll += pnl; n += 1; pnls.append(pnl)
        if pnl > 0: wins += 1
        if bankroll > peak: peak = bankroll
        dd = peak - bankroll
        if dd > max_dd: max_dd = dd
        if bankroll < 50:
            print(f"  *** RUIN at trade {n} ***"); break
    if n == 0:
        print(f"{label}: 0 trades (gated {gated})"); return
    print(f"\n--- {label} ---")
    print(f"  trades:        {n} filled, gated {gated}, no-fill {nofill}, total {len(trades)}")
    print(f"  final $:       ${bankroll:.2f}  (start ${start:.0f})")
    print(f"  net P&L:       ${bankroll-start:+.2f}  ({(bankroll-start)/start*100:+.1f}%)")
    print(f"  win rate:      {wins/n*100:.0f}%")
    print(f"  avg/trade:     ${mean(pnls):+.2f}")
    if n >= 2: print(f"  stdev/trade:   ${stdev(pnls):.2f}")
    print(f"  best trade:    ${max(pnls):+.2f}")
    print(f"  worst trade:   ${min(pnls):+.2f}")
    print(f"  peak bankroll: ${peak:.2f}")
    print(f"  max drawdown:  ${max_dd:.2f}  ({max_dd/peak*100:.0f}%)")


trades = load_shadow_all_paper()
qualifying = [t for t in trades if passes_gates(t)]
print(f"=== NO-MODEL EVENT STRATEGY ===")
print(f"=== {len(trades)} candidate paper trades, {len(qualifying)} pass gates ({len(qualifying)/len(trades)*100:.0f}%) ===")
print(f"=== Gates: spread<=0.04, price NOT in [0.45,0.70), event-whitelist, per-event timing ===")
print(f"=== Slippage {SLIPPAGE*100:.0f}%, fill rate {FILL_PROBABILITY*100:.0f}%, position cap ${POSITION_CAP_DOLLARS:.0f} ===\n")

# Per-event breakdown of qualifying trades
print(f"Qualifying trades by event:")
by_ev = defaultdict(list)
for t in qualifying:
    by_ev[t["event"]].append(per_d_pnl(t))
total_per1 = []
for ev, vs in sorted(by_ev.items(), key=lambda x: -len(x[1])):
    total_per1.extend(vs)
    w = sum(1 for v in vs if v > 0)
    print(f"  {ev:30s}  n={len(vs):>2}  avg={mean(vs):+.3f}/$1  win={w/len(vs)*100:>3.0f}%  best={max(vs):+.3f}  worst={min(vs):+.3f}")
if total_per1:
    w = sum(1 for v in total_per1 if v > 0)
    print(f"  {'TOTAL':30s}  n={len(total_per1):>2}  avg={mean(total_per1):+.3f}/$1  win={w/len(total_per1)*100:>3.0f}%")

# ---- FIXED ----
print("\n" + "="*60)
print("FIXED SIZING")
print("="*60)
for stake in [5, 10, 25, 50]:
    simulate(f"FIXED ${stake}", trades, stake_usd=stake)

# ---- COMPOUND ----
print("\n" + "="*60)
print("COMPOUNDING SIZING")
print("="*60)
for frac in [0.02, 0.05, 0.10, 0.15, 0.20]:
    simulate(f"COMPOUND {frac*100:.0f}%", trades, frac=frac)

# ---- MONTE CARLO ----
print("\n" + "="*60)
print("MONTE CARLO — 500 shuffles")
print("="*60)
def mc(stake, n_runs=500):
    finals = []
    ruins = 0
    for s in range(n_runs):
        rng = random.Random(s)
        shuf = trades[:]
        rng.shuffle(shuf)
        bankroll = 500.0
        for t in shuf:
            if not passes_gates(t): continue
            if rng.random() > FILL_PROBABILITY: continue
            stk = min(max(5.0, stake), POSITION_CAP_DOLLARS)
            if stk > bankroll: continue
            bankroll += per_d_pnl(t) * stk
            if bankroll < 50: ruins += 1; break
        finals.append(bankroll)
    finals.sort()
    p05 = finals[int(0.05*len(finals))]
    p50 = finals[len(finals)//2]
    p95 = finals[int(0.95*len(finals))]
    avg = sum(finals)/len(finals)
    print(f"  FIXED ${stake:>3}:  5th=${p05:>5.0f}  median=${p50:>5.0f}  95th=${p95:>5.0f}  avg=${avg:>5.0f}  ruin={ruins}/{n_runs}")

for stake in [5, 10, 25, 50]:
    mc(stake)
