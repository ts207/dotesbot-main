"""REALISTIC EVENT-STRATEGY BACKTEST.

Replaces the optimistic assumptions of the prior backtest:

  1. SOURCE: shadow_trades.csv (63 paper_buy_yes — the bot's REAL live recommendations
     against actual bid/ask spreads, not retro-generated trades).
  2. EXIT: 60s markout, NOT settlement. We don't actually hold to settle live; bot
     uses model_value_exit / time-stops. 60s is what the bot would realistically exit at.
  3. SLIPPAGE: 8% (not 4%). Live order-book reality: thin Polymarket markets, ghost
     bids, partial fills, FAK rejections.
  4. FEES: 2% on each side baked into slippage.
  5. FILL RATE: 70%. Not every signal gets filled — wide spreads block, no liquidity
     at the model price, etc. We sample-skip 30% to simulate.
  6. POSITION CAP: $50 max regardless of bankroll, because Polymarket order books
     can't absorb more without major slippage.
  7. NO SURVIVORSHIP BIAS: include the realized losers, not just the retro-curated winners.
"""
from __future__ import annotations

import csv
import random
from pathlib import Path
from statistics import mean, stdev

ROOT = Path('/home/tstuv/dota-poly-signal-pnl-asd')

REALISTIC_SLIPPAGE = 0.08          # 4% buy + 4% sell incl. fees
FILL_PROBABILITY = 0.70             # 30% of signals never fill
POSITION_CAP_DOLLARS = 50.0         # max $ per trade regardless of bankroll
RNG = random.Random(42)             # deterministic so re-runs match


def load_shadow_paper_trades():
    """Return list of dicts: {event, fp, markout_60s, markout_30s}."""
    out = []
    path = ROOT / "logs" / "shadow_trades.csv"
    with path.open() as f:
        for row in csv.DictReader(f):
            if row.get("decision") != "paper_buy_yes":
                continue
            try:
                fp = float(row["entry_price"]) if row.get("entry_price") else float(row["ask_at_entry"])
            except (ValueError, KeyError, TypeError):
                continue
            m60 = row.get("markout_60s") or ""
            m30 = row.get("markout_30s") or ""
            if not m60:
                continue
            out.append({
                "event": row.get("event_type", ""),
                "fp": fp,
                "m60": float(m60),
                "m30": float(m30) if m30 else float(m60),
                "spread": float(row.get("spread_at_entry") or 0),
            })
    return out


def per_d_pnl(t):
    """Realistic per-$1 PnL after slippage. Use 60s markout as exit proxy."""
    return (t["m60"] / t["fp"]) - REALISTIC_SLIPPAGE


def simulate(label, trades, *, stake_usd=None, frac=None, start=500.0):
    bankroll = start
    peak = bankroll
    max_dd = 0.0
    n = 0
    wins = 0
    pnls = []
    skipped_filter = 0
    skipped_fill = 0
    ruined = False
    rng = random.Random(42)  # same seed per simulation = comparable
    for t in trades:
        if rng.random() > FILL_PROBABILITY:
            skipped_fill += 1
            continue
        ideal_stake = bankroll * frac if frac is not None else stake_usd
        stake = min(max(5.0, ideal_stake), POSITION_CAP_DOLLARS)
        if stake > bankroll:
            continue
        pnl_dollar = per_d_pnl(t) * stake
        bankroll += pnl_dollar
        n += 1
        pnls.append(pnl_dollar)
        if pnl_dollar > 0:
            wins += 1
        if bankroll > peak:
            peak = bankroll
        dd = peak - bankroll
        if dd > max_dd:
            max_dd = dd
        if bankroll < 50:
            ruined = True
            print(f"  *** RUIN: bankroll ${bankroll:.2f} after trade {n} ***")
            break
    if n == 0:
        print(f"{label}: 0 trades"); return
    print(f"\n--- {label} ---")
    print(f"  trades:        {n} filled, {skipped_fill} no-fill, total signals: {n+skipped_fill}")
    print(f"  final $:       ${bankroll:.2f}  (start ${start:.0f})")
    print(f"  net P&L:       ${bankroll-start:+.2f}  ({(bankroll-start)/start*100:+.1f}%)")
    print(f"  win rate:      {wins/n*100:.0f}%")
    print(f"  avg/trade:     ${mean(pnls):+.2f}")
    if n >= 2: print(f"  stdev/trade:   ${stdev(pnls):.2f}")
    print(f"  best trade:    ${max(pnls):+.2f}")
    print(f"  worst trade:   ${min(pnls):+.2f}")
    print(f"  peak bankroll: ${peak:.2f}")
    print(f"  max drawdown:  ${max_dd:.2f}  ({max_dd/peak*100:.0f}%)")
    if ruined: print(f"  *** SIMULATION RUINED ***")


trades = load_shadow_paper_trades()
print(f"=== REALISTIC EVENT STRATEGY ===")
print(f"=== {len(trades)} live shadow paper_buy_yes from logs/shadow_trades.csv ===")
print(f"=== Slippage: {REALISTIC_SLIPPAGE*100:.0f}%, fill rate: {FILL_PROBABILITY*100:.0f}%, position cap: ${POSITION_CAP_DOLLARS:.0f} ===\n")

# Raw distribution
per1 = [per_d_pnl(t) for t in trades]
print(f"Per-$1 PnL after slippage (60s exit):")
print(f"  avg:       {mean(per1):+.3f}")
print(f"  median:    {sorted(per1)[len(per1)//2]:+.3f}")
print(f"  best:      {max(per1):+.3f}")
print(f"  worst:     {min(per1):+.3f}")
print(f"  win rate:  {sum(1 for v in per1 if v > 0)/len(per1)*100:.0f}%")

from collections import defaultdict
by_ev = defaultdict(list)
for t in trades:
    by_ev[t["event"]].append(per_d_pnl(t))
print("\nPer-event:")
for ev, vs in sorted(by_ev.items(), key=lambda x: -len(x[1])):
    print(f"  {ev:35s}  n={len(vs):>2}  avg={mean(vs):+.3f}  win={sum(1 for v in vs if v > 0)/len(vs)*100:>3.0f}%")

# ---------- FIXED SIZING ----------
print("\n" + "="*60)
print("FIXED SIZING — 8% slippage, 70% fill, $50 cap")
print("="*60)
for stake in [5, 10, 25, 50]:
    simulate(f"FIXED ${stake}", trades, stake_usd=stake)

# ---------- COMPOUNDING ----------
print("\n" + "="*60)
print("COMPOUNDING — % of bankroll (cap at $50/trade)")
print("="*60)
for frac in [0.02, 0.05, 0.10, 0.15, 0.20]:
    simulate(f"COMPOUND {frac*100:.0f}%", trades, frac=frac)

# ---------- MONTE CARLO: shuffle order ----------
print("\n" + "="*60)
print("MONTE CARLO — 200 shuffles of trade order, FIXED $25")
print("="*60)

def mc_run(trades, stake_usd, n_runs=200, start=500.0):
    finals = []
    ruins = 0
    for seed in range(n_runs):
        rng = random.Random(seed)
        shuffled = trades[:]
        rng.shuffle(shuffled)
        bankroll = start
        for t in shuffled:
            if rng.random() > FILL_PROBABILITY:
                continue
            stake = min(max(5.0, stake_usd), POSITION_CAP_DOLLARS)
            if stake > bankroll: continue
            bankroll += per_d_pnl(t) * stake
            if bankroll < 50:
                ruins += 1
                break
        finals.append(bankroll)
    finals.sort()
    p05 = finals[int(0.05*len(finals))]
    p50 = finals[len(finals)//2]
    p95 = finals[int(0.95*len(finals))]
    avg = sum(finals)/len(finals)
    print(f"  FIXED ${stake_usd}:  5th=${p05:.0f}  median=${p50:.0f}  95th=${p95:.0f}  avg=${avg:.0f}  ruin={ruins}/{n_runs}")

for stake in [5, 10, 25, 50]:
    mc_run(trades, stake)
