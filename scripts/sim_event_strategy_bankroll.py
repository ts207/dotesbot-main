"""Backtest the EVENT-DRIVEN strategy on all data, $500 bankroll, fixed + compounding sizing.

Source: logs/backtest_trades.csv (each row = one signal-triggered paper trade with
pnl_settle and pnl_60s computed from real book ticks at a stake of 5 shares).

pnl_settle is in $ at 5-share stake. Convert to per-$1-invested return:
    per_$_return = pnl_settle / (5 * fill_price)
For stopped-out trades, pnl_settle is empty — use pnl_120s as the realized exit P&L.

Bankroll = $500. Each event trade sizes either fixed dollars or fixed %-of-bankroll.
SLIPPAGE = 0.04 (4% drag per fill, conservative for thin Polymarket order books).
"""
from __future__ import annotations

import csv
from pathlib import Path
from statistics import mean, stdev

ROOT = Path('/home/tstuv/dota-poly-signal-pnl-asd')
SLIPPAGE = 0.04


def load_event_trades():
    """Return list of dicts: {event, fill_price, edge, per_d_pnl, settled, raw_settle_$}."""
    out = []
    path = ROOT / "logs" / "backtest_trades.csv"
    with path.open() as f:
        for row in csv.DictReader(f):
            try:
                fp = float(row["fill_price"])
                edge = float(row.get("edge") or 0)
            except (ValueError, KeyError):
                continue
            settle_raw = row.get("pnl_settle", "").strip()
            pnl120_raw = row.get("pnl_120s", "").strip()
            pnl60_raw = row.get("pnl_60s", "").strip()
            settled = bool(settle_raw)
            stake_shares = 5  # 5 shares = $5 × fp dollars
            stake_dollars = stake_shares * fp
            if settled:
                pnl_per_d = float(settle_raw) / stake_dollars
            elif pnl120_raw:
                pnl_per_d = float(pnl120_raw) / stake_dollars
            elif pnl60_raw:
                pnl_per_d = float(pnl60_raw) / stake_dollars
            else:
                continue
            out.append({
                "event": row["event_type"],
                "fill_price": fp,
                "edge": edge,
                "spread": float(row.get("spread") or 0),
                "per_d_pnl": pnl_per_d,
                "settled": settled,
                "stop_loss": row.get("stop_loss_exit", "").lower() == "true",
            })
    return out


def simulate(label, trades, *, stake_usd=None, frac=None, filter_fn=None, start=500.0):
    bankroll = start
    peak = bankroll
    max_dd = 0.0
    n = 0
    wins = 0
    pnls = []
    skipped = 0
    for t in trades:
        if filter_fn and not filter_fn(t):
            skipped += 1
            continue
        stake = max(5.0, bankroll * frac) if frac is not None else stake_usd
        if stake > bankroll:
            continue
        pnl_dollar = (t["per_d_pnl"] - SLIPPAGE) * stake
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
            print(f"  *** RUIN: bankroll dropped below $50 after trade {n} ***")
            break
    if n == 0:
        print(f"{label}: 0 trades"); return
    print(f"\n--- {label} ---")
    print(f"  trades:        {n}/{n+skipped}")
    print(f"  final $:       ${bankroll:.2f}  (start ${start:.0f})")
    print(f"  net P&L:       ${bankroll-start:+.2f}  ({(bankroll-start)/start*100:+.1f}%)")
    print(f"  win rate:      {wins/n*100:.0f}%")
    print(f"  avg/trade:     ${mean(pnls):+.2f}")
    if n >= 2: print(f"  stdev/trade:   ${stdev(pnls):.2f}")
    print(f"  best trade:    ${max(pnls):+.2f}")
    print(f"  worst trade:   ${min(pnls):+.2f}")
    print(f"  peak bankroll: ${peak:.2f}")
    print(f"  max drawdown:  ${max_dd:.2f}  ({max_dd/peak*100:.0f}%)")


trades = load_event_trades()
print(f"=== EVENT STRATEGY: {len(trades)} trades from logs/backtest_trades.csv ===")
print(f"=== SLIPPAGE: -${SLIPPAGE}/$1 stake ===\n")

# Quick stats
per1 = [t["per_d_pnl"] for t in trades]
n_settled = sum(1 for t in trades if t["settled"])
n_stop = sum(1 for t in trades if t["stop_loss"])
print(f"settled-to-game-end: {n_settled}/{len(trades)}, stopped out: {n_stop}")
print(f"raw per-$1 PnL: avg={mean(per1):+.3f}, median={sorted(per1)[len(per1)//2]:+.3f}, "
      f"best={max(per1):+.3f}, worst={min(per1):+.3f}, win={sum(1 for v in per1 if v > 0)/len(per1)*100:.0f}%\n")

# Event-type breakdown
from collections import defaultdict
by_event = defaultdict(list)
for t in trades:
    by_event[t["event"]].append(t["per_d_pnl"])
print("per-event per-$1 stats:")
for ev, vs in sorted(by_event.items(), key=lambda x: -len(x[1])):
    print(f"  {ev:30s}  n={len(vs):>2}  avg={mean(vs):+.3f}  win={sum(1 for v in vs if v > 0)/len(vs)*100:.0f}%")

# ---------------- FIXED SIZING ----------------
print("\n" + "="*60)
print("FIXED SIZING")
print("="*60)
for stake in [10, 25, 50, 75, 100]:
    simulate(f"FIXED ${stake}", trades, stake_usd=stake)

# ---------------- COMPOUNDING SIZING ----------------
print("\n" + "="*60)
print("COMPOUNDING SIZING — % of current bankroll")
print("="*60)
for frac in [0.02, 0.05, 0.10, 0.15, 0.20, 0.25, 0.33, 0.50]:
    simulate(f"COMPOUND {frac*100:.0f}% of bankroll", trades, frac=frac)

# ---------------- FILTERED: high-edge only (>= 0.06) ----------------
print("\n" + "="*60)
print("FILTERED: edge >= 0.06 only")
print("="*60)
def high_edge(t): return t["edge"] >= 0.06
print(f"  {sum(1 for t in trades if high_edge(t))} trades meet filter")
for stake in [25, 50, 75, 100]:
    simulate(f"HE FIXED ${stake}", trades, stake_usd=stake, filter_fn=high_edge)
for frac in [0.05, 0.10, 0.15, 0.20, 0.25, 0.33, 0.50]:
    simulate(f"HE COMPOUND {frac*100:.0f}%", trades, frac=frac, filter_fn=high_edge)

# ---------------- FILTERED: FIGHT_SWING only (best event) ----------------
print("\n" + "="*60)
print("FILTERED: POLL_FIGHT_SWING only (the workhorse event)")
print("="*60)
def fs_only(t): return t["event"] == "POLL_FIGHT_SWING"
for stake in [25, 50, 75, 100]:
    simulate(f"FS FIXED ${stake}", trades, stake_usd=stake, filter_fn=fs_only)
for frac in [0.05, 0.10, 0.15, 0.20, 0.25, 0.33, 0.50]:
    simulate(f"FS COMPOUND {frac*100:.0f}%", trades, frac=frac, filter_fn=fs_only)
