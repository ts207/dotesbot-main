"""Comprehensive shadow_trades.csv audit — actual paper-trade outcomes.

Sources: logs/shadow_trades.csv (the only live-on-bot trade log we have).

Reports:
  1. Per-event win-rate + EV (60s markout as proxy for realized PnL)
  2. Spread bucket analysis (does tight spread = win?)
  3. Price bucket analysis (toss-up zone really losing?)
  4. Game-time bucket analysis (when does signal work?)
  5. Edge calibration — does claimed edge predict realized PnL?
  6. Top wins + top losses (anecdotes)
  7. Honest verdict on whether ANY subset has +EV
"""
from __future__ import annotations
import csv
from collections import defaultdict
from pathlib import Path
from statistics import mean, median, stdev

ROOT = Path('/home/tstuv/dota-poly-signal-pnl-asd')
PATH = ROOT / "logs" / "shadow_trades.csv"


def fnum(s):
    try: return float(s) if s not in ("", None) else None
    except: return None


# Load all paper_buy rows
rows = []
with PATH.open() as f:
    for r in csv.DictReader(f):
        if r.get("decision") != "paper_buy_yes": continue
        ep = fnum(r.get("entry_price"))
        m30 = fnum(r.get("markout_30s"))
        m60 = fnum(r.get("markout_60s"))
        if ep is None or m60 is None: continue
        rows.append({
            "event": r["event_type"],
            "tier": r.get("event_tier", ""),
            "ep": ep,
            "sp": fnum(r.get("spread_at_entry")) or 0,
            "edge": fnum(r.get("executable_edge")) or 0,
            "gt": fnum(r.get("game_time_sec")) or 0,
            "lag": fnum(r.get("lag")) or 0,
            "eq": fnum(r.get("event_quality")) or 0,
            "m30": m30 if m30 is not None else m60,
            "m60": m60,
            "fair": fnum(r.get("fair_price")) or 0,
            "match_id": r.get("match_id", ""),
            "market_name": r.get("market_name", "")[:60],
        })

n = len(rows)
print(f"=== SHADOW TRADES AUDIT ===")
print(f"Total paper_buy_yes: {n}")
if n == 0:
    print("No data."); raise SystemExit
m60_all = [r["m60"] for r in rows]
print(f"Time window: {rows[0]['match_id']} → {rows[-1]['match_id']}")
print()

# Overall
wins = sum(1 for m in m60_all if m > 0)
print(f"=== OVERALL ===")
print(f"  Win rate (60s markout > 0):  {wins/n*100:.0f}%  ({wins}/{n})")
print(f"  Avg markout_60s:              {mean(m60_all):+.4f} ¢/share")
print(f"  Median markout_60s:           {median(m60_all):+.4f}")
print(f"  Stdev:                        {stdev(m60_all):.4f}")
print(f"  Best:                         {max(m60_all):+.4f}")
print(f"  Worst:                        {min(m60_all):+.4f}")
print(f"  Sharpe-ish:                   {mean(m60_all)/stdev(m60_all):.3f}")

# Per-event
print(f"\n=== PER-EVENT (sorted by sample size) ===")
by_ev = defaultdict(list)
for r in rows:
    by_ev[r["event"]].append(r)
print(f"  {'event':35s} {'n':>3s} {'win%':>5s} {'avg_m60':>9s} {'med':>9s} {'best':>7s} {'worst':>7s}")
for ev, rs in sorted(by_ev.items(), key=lambda x: -len(x[1])):
    ms = [r["m60"] for r in rs]
    w = sum(1 for m in ms if m > 0)
    print(f"  {ev:35s} {len(rs):>3} {w/len(rs)*100:>4.0f}% {mean(ms):+9.4f} {median(ms):+9.4f} {max(ms):+7.3f} {min(ms):+7.3f}")

# Spread buckets
print(f"\n=== SPREAD BUCKETS ===")
def bucket(rs, key, edges):
    print(f"  {'bucket':>10s} {'n':>3s} {'win%':>5s} {'avg_m60':>9s}")
    for lo, hi in edges:
        ss = [r for r in rs if lo <= key(r) < hi]
        if not ss: continue
        ms = [r["m60"] for r in ss]
        w = sum(1 for m in ms if m > 0)
        print(f"  [{lo:.2f},{hi:.2f}) {len(ss):>3} {w/len(ss)*100:>4.0f}% {mean(ms):+9.4f}")
bucket(rows, lambda r: r["sp"], [(0, 0.02), (0.02, 0.04), (0.04, 0.07), (0.07, 0.15), (0.15, 1.0)])

# Price buckets
print(f"\n=== PRICE BUCKETS ===")
bucket(rows, lambda r: r["ep"], [(0, 0.30), (0.30, 0.45), (0.45, 0.55), (0.55, 0.70), (0.70, 1.0)])

# Game time
print(f"\n=== GAME TIME BUCKETS (sec) ===")
bucket(rows, lambda r: r["gt"], [(0, 600), (600, 1200), (1200, 1800), (1800, 2400), (2400, 9999)])

# Edge buckets
print(f"\n=== CLAIMED EDGE BUCKETS ===")
bucket(rows, lambda r: r["edge"], [(0, 0.03), (0.03, 0.05), (0.05, 0.08), (0.08, 0.12), (0.12, 1.0)])

# Calibration: claimed edge vs realized
print(f"\n=== EDGE CALIBRATION ===")
xs = [r["edge"] for r in rows]
ys = [r["m60"] for r in rows]
mu_x, mu_y = mean(xs), mean(ys)
cov = sum((xs[i]-mu_x)*(ys[i]-mu_y) for i in range(n)) / n
sx, sy = stdev(xs), stdev(ys)
corr = cov / (sx * sy) if sx > 0 and sy > 0 else 0
print(f"  Claimed avg edge:  {mu_x:+.4f}")
print(f"  Realized avg m60:  {mu_y:+.4f}")
print(f"  Realized/claimed:  {mu_y/mu_x*100:+.0f}%  (100% = perfect calibration)")
print(f"  Correlation:        {corr:+.3f}  (~0 = model has no predictive power)")

# Top wins + losses
print(f"\n=== TOP 5 WINS ===")
for r in sorted(rows, key=lambda x: -x["m60"])[:5]:
    print(f"  +{r['m60']:.3f}  {r['event']:30s} ep={r['ep']:.2f} sp={r['sp']:.2f} gt={int(r['gt'])}s  {r['market_name']}")
print(f"\n=== TOP 5 LOSSES ===")
for r in sorted(rows, key=lambda x: x["m60"])[:5]:
    print(f"  {r['m60']:+.3f}  {r['event']:30s} ep={r['ep']:.2f} sp={r['sp']:.2f} gt={int(r['gt'])}s  {r['market_name']}")

# Honest subset analysis: filter on the OPTION C config and see EV
print(f"\n=== OPTION C SIMULATION ===")
print(f"Apply current NOMODEL gates to historical shadow data:")
print(f"  whitelist (9 events), spread<=0.05, game_time>=300, NO price gate, NO kd/nw gate")
WL = {"OBJECTIVE_CONVERSION_T2","POLL_BUYBACK_CAPITULATION","POLL_COMEBACK_RECOVERY",
      "POLL_DECISIVE_STOMP","POLL_FIGHT_SWING","POLL_KILL_BURST_CONFIRMED",
      "POLL_LATE_FIGHT_FLIP","POLL_STRUCTURAL_DOMINANCE","POLL_VALUE_DISAGREEMENT"}
filt = [r for r in rows if r["event"] in WL and r["sp"] <= 0.05 and r["gt"] >= 300]
if filt:
    ms = [r["m60"] for r in filt]
    w = sum(1 for m in ms if m > 0)
    print(f"  After filter: {len(filt)}/{n} ({len(filt)/n*100:.0f}% of paper trades)")
    print(f"  Win rate:      {w/len(filt)*100:.0f}%")
    print(f"  Avg m60:       {mean(ms):+.4f} c/share")
    print(f"  At $50 stake (≈100 shares):  avg ${mean(ms)*100:+.2f}/trade")
    print(f"  Total P&L on $50/trade:      ${sum(ms)*100:+.0f}")
