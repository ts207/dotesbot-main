"""Deep-dive analysis of shadow_trades.csv across multiple angles.

Goes beyond per-cell EV to look at:
  1. Markout horizon evolution (3s → 10s → 30s → 60s) — does signal grow or decay?
  2. League-specific performance (different tournaments behave differently)
  3. Per-side bias (YES vs NO — model directional bias?)
  4. Per-match clustering (are losses concentrated on specific matches?)
  5. Loss-size distribution (fat tails or symmetric?)
  6. fair_price vs realized — full calibration curve
  7. Per-event horizon decay (which signal type fades fastest)
  8. Sharpe by sub-strategy
"""
from __future__ import annotations
import csv
from collections import defaultdict
from pathlib import Path
from statistics import mean, median, stdev, quantiles

ROOT = Path('/home/tstuv/dota-poly-signal-pnl-asd')

def fnum(s):
    try: return float(s) if s not in ("", None) else None
    except: return None

rows = []
with (ROOT/"logs/shadow_trades.csv").open() as f:
    for r in csv.DictReader(f):
        if r.get("decision") != "paper_buy_yes": continue
        ep = fnum(r.get("entry_price"))
        m3 = fnum(r.get("markout_3s")); m10 = fnum(r.get("markout_10s"))
        m30 = fnum(r.get("markout_30s")); m60 = fnum(r.get("markout_60s"))
        if ep is None or m60 is None: continue
        rows.append({
            "event": r["event_type"], "ep": ep,
            "sp": fnum(r.get("spread_at_entry")) or 0,
            "gt": fnum(r.get("game_time_sec")) or 0,
            "edge": fnum(r.get("executable_edge")) or 0,
            "fair": fnum(r.get("fair_price")) or ep,
            "lag": fnum(r.get("lag")) or 0,
            "m3": m3 if m3 is not None else 0,
            "m10": m10 if m10 is not None else 0,
            "m30": m30 if m30 is not None else m60,
            "m60": m60,
            "ts": r.get("timestamp_utc", ""),
            "side": r.get("side", ""),
            "match_id": r.get("match_id", ""),
            "market_name": r.get("market_name", "")[:50],
        })
n = len(rows)

# ============================================================
# 1. Horizon evolution — does signal grow or decay?
# ============================================================
print(f"=== MARKOUT HORIZON EVOLUTION (n={n}) ===")
print(f"  {'horizon':>8s} {'avg':>9s} {'med':>9s} {'win%':>5s} {'positive_share':>14s}")
for hkey, hlabel in [("m3","3s"), ("m10","10s"), ("m30","30s"), ("m60","60s")]:
    vals = [r[hkey] for r in rows if r[hkey] is not None]
    if not vals: continue
    pos = sum(1 for v in vals if v > 0)
    print(f"  {hlabel:>8s} {mean(vals):+9.4f} {median(vals):+9.4f} {pos/len(vals)*100:>4.0f}% {pos/len(vals)*100:>13.0f}%")
print(f"\n  → If 60s is best, signal GROWS. If 3s is best, signal already priced-in.")

# ============================================================
# 2. League-specific (extract from match_id heuristic)
# ============================================================
print(f"\n=== PER-MATCH CLUSTERING ===")
by_match = defaultdict(list)
for r in rows: by_match[r["match_id"]].append(r["m60"])
print(f"  Unique matches with paper trades: {len(by_match)}")
match_pnl = [(mid, sum(ms), len(ms)) for mid, ms in by_match.items()]
match_pnl.sort(key=lambda x: -x[1])
print(f"\n  TOP 5 winning matches:")
for mid, pnl, count in match_pnl[:5]:
    print(f"    {mid:>12s}  n={count:>2d}  total={pnl:+.3f}")
print(f"\n  TOP 5 losing matches:")
for mid, pnl, count in match_pnl[-5:]:
    print(f"    {mid:>12s}  n={count:>2d}  total={pnl:+.3f}")
total_pnl = sum(p for _, p, _ in match_pnl)
top5_pnl = sum(p for _, p, _ in match_pnl[:5])
bot5_pnl = sum(p for _, p, _ in match_pnl[-5:])
print(f"\n  Concentration: top-5 matches = {top5_pnl/abs(total_pnl)*100 if total_pnl else 0:.0f}% of P&L")
print(f"                 bot-5 matches = {bot5_pnl/abs(total_pnl)*100 if total_pnl else 0:.0f}% of P&L")

# ============================================================
# 3. Per-side bias (YES vs NO)
# ============================================================
print(f"\n=== PER-SIDE (YES vs NO) ===")
yes = [r["m60"] for r in rows if r["side"] == "YES"]
no = [r["m60"] for r in rows if r["side"] == "NO"]
print(f"  YES: n={len(yes)}  win={sum(1 for v in yes if v > 0)/max(len(yes),1)*100:.0f}%  avg={mean(yes) if yes else 0:+.4f}")
print(f"  NO:  n={len(no)}  win={sum(1 for v in no if v > 0)/max(len(no),1)*100:.0f}%  avg={mean(no) if no else 0:+.4f}")

# ============================================================
# 4. Loss-size distribution (fat tails?)
# ============================================================
print(f"\n=== LOSS SIZE DISTRIBUTION ===")
losses = sorted([r["m60"] for r in rows if r["m60"] < 0])
wins = sorted([r["m60"] for r in rows if r["m60"] > 0], reverse=True)
print(f"  losses: n={len(losses)}  avg={mean(losses) if losses else 0:.3f}  worst={min(losses) if losses else 0:.3f}")
print(f"  wins:   n={len(wins)}  avg={mean(wins) if wins else 0:.3f}  best={max(wins) if wins else 0:.3f}")
if losses and wins:
    print(f"  avg_win / avg_loss ratio: {abs(mean(wins)/mean(losses)):.2f}")
    print(f"  best/worst ratio:         {max(wins)/abs(min(losses)):.2f}")
    print(f"  Profit factor:  {sum(wins)/abs(sum(losses)):.2f}  (≥1 = profitable)")

# ============================================================
# 5. fair_price vs realized (calibration curve)
# ============================================================
print(f"\n=== FAIR_PRICE vs REALIZED 60s ===")
# realized_price_at_60s = entry + m60
# fair_price is what model said
# delta = fair - entry = what model expected to gain
# realized = m60 = what actually happened
deltas_buckets = [(0, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, 1.0)]
print(f"  {'fair - entry':>14s} {'n':>3s} {'avg_realized':>13s} {'win%':>5s}")
for lo, hi in deltas_buckets:
    sub = [r for r in rows if lo <= (r["fair"] - r["ep"]) < hi]
    if not sub: continue
    ms = [r["m60"] for r in sub]
    w = sum(1 for v in ms if v > 0)
    print(f"  [{lo:.2f},{hi:.2f})    {len(sub):>3d} {mean(ms):+13.4f} {w/len(sub)*100:>4.0f}%")
print(f"  → If realized scales with fair-entry, model is calibrated. Flat = useless.")

# ============================================================
# 6. Lag impact
# ============================================================
print(f"\n=== LAG IMPACT (Steam-vs-book latency, lower = fresher) ===")
print(f"  {'lag':>10s} {'n':>3s} {'avg_m60':>9s} {'win%':>5s}")
for lo, hi in [(0, 0.04), (0.04, 0.08), (0.08, 0.15), (0.15, 0.50)]:
    sub = [r for r in rows if lo <= r["lag"] < hi]
    if not sub: continue
    ms = [r["m60"] for r in sub]
    w = sum(1 for v in ms if v > 0)
    print(f"  [{lo:.2f},{hi:.2f})    {len(sub):>3} {mean(ms):+9.4f} {w/len(sub)*100:>4.0f}%")

# ============================================================
# 7. Per-event HORIZON DECAY (does signal fade fast?)
# ============================================================
print(f"\n=== PER-EVENT HORIZON DECAY ===")
print(f"  {'event':25s} {'n':>3s} {'m3':>9s} {'m10':>9s} {'m30':>9s} {'m60':>9s}")
by_ev = defaultdict(list)
for r in rows: by_ev[r["event"]].append(r)
for ev, rs in sorted(by_ev.items(), key=lambda x: -len(x[1])):
    if len(rs) < 3: continue
    def avg(k): return mean([r[k] for r in rs if r[k] is not None])
    print(f"  {ev:25s} {len(rs):>3d} {avg('m3'):+9.4f} {avg('m10'):+9.4f} {avg('m30'):+9.4f} {avg('m60'):+9.4f}")

# ============================================================
# 8. Time-of-day P&L
# ============================================================
print(f"\n=== TIME-OF-DAY (UTC hour) ===")
by_hour = defaultdict(list)
for r in rows:
    try:
        h = int(r["ts"][11:13])
        by_hour[h].append(r["m60"])
    except: pass
print(f"  {'hour_utc':>8s} {'n':>3s} {'avg_m60':>9s} {'win%':>5s}")
for h in sorted(by_hour.keys()):
    ms = by_hour[h]
    w = sum(1 for v in ms if v > 0)
    print(f"  {h:>8d} {len(ms):>3d} {mean(ms):+9.4f} {w/len(ms)*100:>4.0f}%")
