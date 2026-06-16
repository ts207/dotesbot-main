"""Three-gate strategy backtest: price >= 0.70, spread <= 0.04, game_time >= 1500."""
import csv, random
from pathlib import Path
from statistics import mean, stdev
ROOT = Path('/home/tstuv/dota-poly-signal-pnl-asd')

def fnum(s):
    try: return float(s) if s not in ("", None) else None
    except: return None

trades = []
with (ROOT / "logs" / "shadow_trades.csv").open() as f:
    for row in csv.DictReader(f):
        if row.get("decision") != "paper_buy_yes": continue
        ep = fnum(row.get("entry_price")) or fnum(row.get("ask_at_entry"))
        sp = fnum(row.get("spread_at_entry"))
        gt = fnum(row.get("game_time_sec"))
        m60 = fnum(row.get("markout_60s"))
        if None in (ep, sp, gt, m60): continue
        trades.append({"event": row["event_type"], "ep": ep, "sp": sp, "gt": gt, "m60": m60})

def passes(t): return t["ep"] >= 0.70 and t["sp"] <= 0.04 and t["gt"] >= 1500

q = [t for t in trades if passes(t)]
m = [t["m60"]/t["ep"] - 0.04 for t in q]
w = sum(1 for v in m if v > 0)
print(f"Three-gate qualifying: {len(q)}/{len(trades)} ({len(q)/len(trades)*100:.0f}%)")
print(f"Per-$1 PnL: avg={mean(m):+.4f}, win={w/len(q)*100:.0f}%, best={max(m):+.4f}, worst={min(m):+.4f}")
print(f"Stdev: {stdev(m):.4f}, sharpe-ish: {mean(m)/stdev(m):.2f}")

print("\nPer-event:")
from collections import defaultdict
by_ev = defaultdict(list)
for t in q: by_ev[t["event"]].append(t["m60"]/t["ep"] - 0.04)
for ev, vs in sorted(by_ev.items(), key=lambda x: -len(x[1])):
    w = sum(1 for v in vs if v > 0)
    print(f"  {ev:35s}  n={len(vs):>2}  avg={mean(vs):+.4f}/$1  win={w/len(vs)*100:>3.0f}%")

print("\n$500 BANKROLL SIM (3-gate strategy, 85% fill, $50 cap)")
def sim(stake_usd=None, frac=None, label="", seed=42):
    rng = random.Random(seed)
    bk = 500.0; peak = 500; max_dd = 0; n = 0; wins = 0; pnls = []
    for t in trades:
        if not passes(t): continue
        if rng.random() > 0.85: continue
        ideal = bk * frac if frac is not None else stake_usd
        stake = min(max(5.0, ideal), 50.0)
        if stake > bk: continue
        pnl = (t["m60"]/t["ep"] - 0.04) * stake
        bk += pnl; n += 1; pnls.append(pnl)
        if pnl > 0: wins += 1
        if bk > peak: peak = bk
        max_dd = max(max_dd, peak - bk)
    if n == 0: print(f"  {label}: 0 trades"); return
    print(f"  {label}: n={n}  ${bk:.0f} ({(bk-500)/500*100:+.1f}%)  win {wins/n*100:.0f}%  "
          f"avg ${mean(pnls):+.2f}  best ${max(pnls):+.2f}  worst ${min(pnls):+.2f}  maxDD {max_dd/peak*100:.0f}%")

for s in [10, 25, 50]:
    sim(stake_usd=s, label=f"FIXED ${s}")
for f in [0.02, 0.05, 0.10, 0.20]:
    sim(frac=f, label=f"COMPOUND {f*100:.0f}%")

# Monte carlo
print("\nMonte Carlo (500 shuffles):")
def mc(stake_usd):
    finals, ruins = [], 0
    for s in range(500):
        rng = random.Random(s)
        shuf = trades[:]; rng.shuffle(shuf)
        bk = 500.0
        for t in shuf:
            if not passes(t): continue
            if rng.random() > 0.85: continue
            stk = min(max(5.0, stake_usd), 50.0)
            if stk > bk: continue
            bk += (t["m60"]/t["ep"] - 0.04) * stk
            if bk < 50: ruins += 1; break
        finals.append(bk)
    finals.sort()
    print(f"  FIXED ${stake_usd:>3}: 5th=${finals[25]:.0f}  med=${finals[250]:.0f}  95th=${finals[475]:.0f}  ruin={ruins}/500")
for s in [10, 25, 50]:
    mc(s)
