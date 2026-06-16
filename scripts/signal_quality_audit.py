"""SIGNAL QUALITY AUDIT — figure out why retro backtest +27% becomes live −12%.

Checks:
  1. EDGE CALIBRATION    — does bot's claimed edge predict realized 60s PnL?
  2. FAIR-PRICE BIAS     — fair_price vs realized 60s mid (is model directionally right?)
  3. EVENT EV BY GAME-TIME  — early vs late game signal quality
  4. SPREAD IMPACT       — wide spread = worse outcomes?
  5. LAG IMPACT          — stale signals = adverse selection?
  6. SKIP REASONS        — what's the bot rejecting most + would those be wins?
  7. PRICE-BUCKET BIAS   — does the model do better at extreme prices vs 0.50?
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from statistics import mean, median, stdev

ROOT = Path('/home/tstuv/dota-poly-signal-pnl-asd')


def fnum(s):
    try: return float(s) if s not in ("", None) else None
    except: return None


def load_shadow():
    out = []
    with (ROOT / "logs" / "shadow_trades.csv").open() as f:
        for row in csv.DictReader(f):
            r = {k: fnum(v) if v not in ("", None, "True", "False") else v for k, v in row.items()}
            r["raw_event"] = row["event_type"]
            r["raw_decision"] = row["decision"]
            r["raw_skip"] = row.get("skip_reason", "")
            out.append(r)
    return out


rows = load_shadow()
paper = [r for r in rows if r["raw_decision"] == "paper_buy_yes"]
skips = [r for r in rows if r["raw_decision"] == "skip"]
print(f"Loaded {len(rows)} signals: {len(paper)} paper_buy, {len(skips)} skip\n")


# ---------- 1. EDGE CALIBRATION ----------
print("="*70)
print("1) EDGE CALIBRATION — claimed edge vs realized 60s markout")
print("="*70)
print("If the model is well-calibrated, higher claimed edge → higher realized PnL.\n")
buckets = [(0.00, 0.03), (0.03, 0.05), (0.05, 0.08), (0.08, 0.12), (0.12, 1.00)]
print(f"  {'edge bucket':>14}  {'n':>3}  {'avg_markout':>12}  {'med_markout':>12}  {'win%':>5}  {'avg_edge':>9}")
for lo, hi in buckets:
    rs = [r for r in paper if r.get("executable_edge") is not None
          and lo <= r["executable_edge"] < hi
          and r.get("markout_60s") is not None]
    if not rs: continue
    m = [r["markout_60s"] for r in rs]
    e = [r["executable_edge"] for r in rs]
    w = sum(1 for v in m if v > 0)
    print(f"  [{lo:.2f},{hi:.2f})    {len(rs):>3}  {mean(m):+10.4f}    {median(m):+10.4f}   {w/len(rs)*100:>4.0f}%  {mean(e):+8.4f}")

# ---------- 2. FAIR PRICE BIAS ----------
print()
print("="*70)
print("2) FAIR PRICE vs REALIZED 60s — direction check")
print("="*70)
print("realized_60s_mid ≈ entry_price + markout_60s.")
print("If fair_price > entry_price → model says BUY. Did price actually rise?\n")
correct_dir = 0
agg = []
for r in paper:
    fp = r.get("fair_price"); ep = r.get("entry_price"); m60 = r.get("markout_60s")
    if None in (fp, ep, m60): continue
    model_says_buy = fp > ep
    price_rose = m60 > 0
    if model_says_buy == price_rose: correct_dir += 1
    agg.append({"fp": fp, "ep": ep, "m60": m60, "delta": fp - ep})
n = len(agg)
print(f"  Direction correct: {correct_dir}/{n} = {correct_dir/n*100:.0f}%  (chance = 50%)")
print(f"  All trades are BUYS so model_says_buy is always True.")
print(f"  → Rephrased: does price rise after the model says BUY?")
rose = sum(1 for a in agg if a["m60"] > 0)
print(f"  Price rose in 60s: {rose}/{n} = {rose/n*100:.0f}%  (chance = 50%)")

# ---------- 3. EVENT EV BY GAME TIME ----------
print()
print()
print("="*70)
print("3) EVENT EV BY GAME-TIME BUCKET (per-event, when does signal work?)")
print("="*70)
gt_buckets = [(0, 600, "early <10m"), (600, 1500, "mid 10-25m"), (1500, 2400, "late 25-40m"), (2400, 9999, "vlate >40m")]
print(f"\n  {'event':30s} {'bucket':>14s} {'n':>3} {'avg_m60':>9} {'win%':>5}")
for ev in sorted(set(r["raw_event"] for r in paper)):
    by_g = defaultdict(list)
    for r in paper:
        if r["raw_event"] != ev: continue
        gt = r.get("game_time_sec"); m = r.get("markout_60s")
        if gt is None or m is None: continue
        for lo, hi, lbl in gt_buckets:
            if lo <= gt < hi:
                by_g[lbl].append(m); break
    for lbl in [g[2] for g in gt_buckets]:
        vs = by_g.get(lbl, [])
        if not vs: continue
        w = sum(1 for v in vs if v > 0)
        print(f"  {ev:30s} {lbl:>14s} {len(vs):>3} {mean(vs):+9.4f} {w/len(vs)*100:>4.0f}%")

# ---------- 4. SPREAD IMPACT ----------
print()
print("="*70)
print("4) SPREAD IMPACT — wider spread = worse adverse selection?")
print("="*70)
sp_buckets = [(0, 0.02), (0.02, 0.04), (0.04, 0.07), (0.07, 0.15), (0.15, 1.0)]
print(f"\n  {'spread':>14}  {'n':>3}  {'avg_m60':>9}  {'win%':>5}")
for lo, hi in sp_buckets:
    rs = [r for r in paper if r.get("spread_at_entry") is not None
          and lo <= r["spread_at_entry"] < hi
          and r.get("markout_60s") is not None]
    if not rs: continue
    m = [r["markout_60s"] for r in rs]
    w = sum(1 for v in m if v > 0)
    print(f"  [{lo:.2f},{hi:.2f})    {len(rs):>3}  {mean(m):+8.4f}  {w/len(rs)*100:>4.0f}%")

# ---------- 5. LAG IMPACT ----------
print()
print("="*70)
print("5) LAG IMPACT — does old book data cause losses?")
print("="*70)
lag_buckets = [(0, 0.04), (0.04, 0.08), (0.08, 0.15), (0.15, 0.50)]
print(f"\n  {'lag bucket':>14}  {'n':>3}  {'avg_m60':>9}  {'win%':>5}  {'avg_edge':>9}")
for lo, hi in lag_buckets:
    rs = [r for r in paper if r.get("lag") is not None
          and lo <= r["lag"] < hi
          and r.get("markout_60s") is not None]
    if not rs: continue
    m = [r["markout_60s"] for r in rs]
    e = [r.get("executable_edge", 0) for r in rs]
    w = sum(1 for v in m if v > 0)
    print(f"  [{lo:.2f},{hi:.2f})    {len(rs):>3}  {mean(m):+8.4f}  {w/len(rs)*100:>4.0f}%  {mean(e):+8.4f}")

# ---------- 6. SKIP REASONS — would they have won? ----------
print()
print("="*70)
print("6) SKIP REASONS — top reasons + would-have-been markout outcome")
print("="*70)
skip_groups = defaultdict(list)
for r in skips:
    reason = r["raw_skip"].split(":")[0]
    m = r.get("markout_60s")
    if isinstance(m, str): m = fnum(m)
    skip_groups[reason].append(m)
print(f"\n  {'reason':35s}  {'n':>3}  {'have_m60':>8}  {'avg_m60':>9}  {'win%':>5}")
for reason, ms in sorted(skip_groups.items(), key=lambda x: -len(x[1]))[:20]:
    have = [m for m in ms if m is not None]
    if not have:
        print(f"  {reason:35s}  {len(ms):>3}  {len(have):>8}    n/a       n/a")
        continue
    w = sum(1 for v in have if v > 0)
    print(f"  {reason:35s}  {len(ms):>3}  {len(have):>8}  {mean(have):+8.4f}  {w/len(have)*100:>4.0f}%")

# ---------- 7. PRICE BUCKET BIAS ----------
print()
print("="*70)
print("7) PRICE BUCKET — does model work better at extreme prices?")
print("="*70)
price_buckets = [(0, 0.30), (0.30, 0.45), (0.45, 0.55), (0.55, 0.70), (0.70, 1.0)]
print(f"\n  {'entry price':>14}  {'n':>3}  {'avg_m60':>9}  {'win%':>5}")
for lo, hi in price_buckets:
    rs = [r for r in paper if r.get("entry_price") is not None
          and lo <= r["entry_price"] < hi
          and r.get("markout_60s") is not None]
    if not rs: continue
    m = [r["markout_60s"] for r in rs]
    w = sum(1 for v in m if v > 0)
    print(f"  [{lo:.2f},{hi:.2f})    {len(rs):>3}  {mean(m):+8.4f}  {w/len(rs)*100:>4.0f}%")

# ---------- 8. RAW EXPECTED VS REALIZED ----------
print()
print("="*70)
print("8) CLAIMED EDGE vs REALIZED — model accuracy summary")
print("="*70)
agg = []
for r in paper:
    e = r.get("executable_edge"); m = r.get("markout_60s")
    if e is None or m is None: continue
    agg.append((e, m))
if agg:
    e_vals = [a[0] for a in agg]; m_vals = [a[1] for a in agg]
    print(f"  Claimed avg edge:   {mean(e_vals):+.4f}")
    print(f"  Realized avg 60s:   {mean(m_vals):+.4f}")
    print(f"  Realized/claimed ratio: {mean(m_vals)/mean(e_vals)*100:+.0f}%")
    print(f"  (100% = perfect calibration; 0% = no edge realized; negative = adverse selection)")
    # Correlation
    n = len(agg)
    mu_e = mean(e_vals); mu_m = mean(m_vals)
    cov = sum((e-mu_e)*(m-mu_m) for e, m in agg) / n
    sd_e = stdev(e_vals); sd_m = stdev(m_vals)
    print(f"  Correlation(edge, realized_60s): {cov/(sd_e*sd_m):+.3f}")
    print(f"  (should be near +1.0 if model is good; near 0 if random; negative if anti-signal)")
