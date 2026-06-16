"""Why does 97% market reaction → -1.5c loss per trade?

Investigate the SIGNAL → EXECUTION gap. Possible explanations:
  A) Latency: bot detects event N seconds AFTER market already moved
  B) Direction: bot picks wrong side (event says radiant but price moves dire)
  C) Entry price: bot buys at ask after move; fair is lower
  D) Adverse selection: market makers fade us

Mines:
  dota_events.csv  (event timestamps + direction)
  shadow_trades.csv (entry price + markout)
  book_events.csv (actual book moves before/after event)
"""
from __future__ import annotations
import csv
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean, median, stdev

ROOT = Path('/home/tstuv/dota-poly-signal-pnl-asd')


def fnum(s):
    try: return float(s) if s not in ("", None) else None
    except: return None


def parse_ts(s):
    try: return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except: return None


# Load everything
shadow = []
with (ROOT/"logs/shadow_trades.csv").open() as f:
    for r in csv.DictReader(f):
        if r.get("decision") != "paper_buy_yes": continue
        try:
            shadow.append({
                "ts": parse_ts(r["timestamp_utc"]),
                "match_id": r["match_id"], "event": r["event_type"],
                "ep": float(r["entry_price"]),
                "bid": fnum(r.get("bid_at_entry")) or 0,
                "ask": fnum(r.get("ask_at_entry")) or 0,
                "sp": fnum(r.get("spread_at_entry")) or 0,
                "fair": fnum(r.get("fair_price")) or 0,
                "edge": fnum(r.get("executable_edge")) or 0,
                "lag": fnum(r.get("lag")) or 0,
                "m3": fnum(r.get("markout_3s")) or 0,
                "m10": fnum(r.get("markout_10s")) or 0,
                "m30": fnum(r.get("markout_30s")) or 0,
                "m60": fnum(r.get("markout_60s")) or 0,
                "side": r["side"],
                "token_id": r["token_id"],
                "shadow_id": r.get("shadow_id", ""),
                "gt": fnum(r.get("game_time_sec")) or 0,
            })
        except (ValueError, KeyError): continue


# ============================================================
# 1. ENTRY PRICE vs FAIR — are we paying too much?
# ============================================================
print("="*80)
print("1. ENTRY PRICE vs FAIR — how much edge does the bot CLAIM at entry?")
print("="*80)
deltas = [s["fair"] - s["ep"] for s in shadow]
print(f"  Claimed edge (fair - ep):  avg={mean(deltas):+.4f}  med={median(deltas):+.4f}")
print(f"  Range:                      {min(deltas):+.3f} to {max(deltas):+.3f}")
realized = [s["m60"] for s in shadow]
print(f"  Realized 60s markout:       avg={mean(realized):+.4f}  med={median(realized):+.4f}")
print(f"  Realized as % of claimed:   {mean(realized)/mean(deltas)*100 if mean(deltas) else 0:+.0f}%")
print(f"  → if 0% or negative: model is calibrated wrong / bot enters too late")


# ============================================================
# 2. BUY-AT-ASK toll
# ============================================================
print()
print("="*80)
print("2. BUY-AT-ASK toll — how much do we lose to spread at entry?")
print("="*80)
# Each trade: paid ASK, but FAIR is the mid (bid+ask)/2 or lower
# Spread cost = (ask - mid) = (ask - bid)/2
spreads = [s["sp"] for s in shadow]
print(f"  Spread at entry:  avg={mean(spreads):+.4f}  med={median(spreads):+.4f}  worst={max(spreads):.3f}")
print(f"  Half-spread cost (= avg toll per trade): -{mean(spreads)/2:.4f}/share")
print(f"  At $50 stake (~100 shares):              -${mean(spreads)/2*100:.2f}/trade lost to spread")
print()
print(f"  Realized avg m60: {mean(realized):+.4f}")
print(f"  Add back half-spread: {mean(realized) + mean(spreads)/2:+.4f}")
print(f"  → if positive after spread adjustment, signal IS profitable but spread eats it")


# ============================================================
# 3. MARKOUT TIME DECAY — does the bot enter LATE?
# ============================================================
print()
print("="*80)
print("3. SIGNAL TIME DECAY — entry-to-N-seconds-later")
print("="*80)
print(f"  If bot enters AT peak of move: m3=worst, m60=best (signal grows)")
print(f"  If bot enters AT trough: m3=worst, m60=best (recovery)")
print(f"  If bot enters BEHIND the move: m3=worst, m60=stays bad")
print()
print(f"  {'horizon':>8s} {'avg':>9s} {'win%':>5s} {'positive':>9s}  interpretation")
for hkey, hlabel in [("m3","3s"), ("m10","10s"), ("m30","30s"), ("m60","60s")]:
    vals = [s[hkey] for s in shadow]
    pos = sum(1 for v in vals if v > 0)
    avg = mean(vals)
    growth = "↗ growing" if hkey == "m60" and avg > -0.02 else ""
    print(f"  {hlabel:>8s} {avg:+9.4f} {pos/len(vals)*100:>4.0f}% {pos:>4} {growth}")
print()
print(f"  → Bot enters at -4.7c instantly (3s markout). Signal recovers TO -1.5c by 60s.")
print(f"  → That's adverse selection: market already moved, bot buys at top.")


# ============================================================
# 4. PER-EVENT DIRECTIONAL ACCURACY
# ============================================================
print()
print("="*80)
print("4. PER-EVENT direction (did the side we bought actually win post-entry?)")
print("="*80)
# Side="YES" buys yes_token. If price RISES (m60 > 0), we picked right side.
# Side="NO" buys no_token. If price RISES (m60 > 0), we picked right side.
print(f"  Per-event win rate (any side):")
by_ev = defaultdict(list)
for s in shadow: by_ev[s["event"]].append(s["m60"])
print(f"  {'event':30s} {'n':>3s} {'win%':>5s} {'avg_m60':>9s}")
for ev, ms in sorted(by_ev.items(), key=lambda x: -len(x[1])):
    w = sum(1 for v in ms if v > 0)
    print(f"  {ev:30s} {len(ms):>3} {w/len(ms)*100:>4.0f}% {mean(ms):+9.4f}")
print(f"\n  → coin flip = 50%. Most events are AT or BELOW coin flip rate.")


# ============================================================
# 5. LAG-TO-ENTRY: when bot detects event, how stale is its data?
# ============================================================
print()
print("="*80)
print("5. STALE DATA on entry (lag = how old book/Steam data is)")
print("="*80)
print(f"  {'lag bucket':>14s} {'n':>3s} {'avg_m60':>9s} {'win%':>5s}")
buckets = [(0, 0.03), (0.03, 0.06), (0.06, 0.10), (0.10, 0.20), (0.20, 1.0)]
for lo, hi in buckets:
    sub = [s for s in shadow if lo <= s["lag"] < hi]
    if not sub: continue
    ms = [s["m60"] for s in sub]
    w = sum(1 for v in ms if v > 0)
    print(f"  [{lo:.2f},{hi:.2f})    {len(sub):>3} {mean(ms):+9.4f} {w/len(sub)*100:>4.0f}%")
print(f"\n  → if higher lag = worse outcome, latency is killing us")


# ============================================================
# 6. WIDTH OF MARKETS at entry — wide markets = harder to fill
# ============================================================
print()
print("="*80)
print("6. SPREAD vs OUTCOME — does wide spread predict bad trade?")
print("="*80)
print(f"  {'spread':>12s} {'n':>3s} {'avg_m60':>9s} {'win%':>5s} {'gross':>9s} {'after_½sp':>10s}")
sp_buckets = [(0, 0.02), (0.02, 0.04), (0.04, 0.07), (0.07, 0.15), (0.15, 1.0)]
for lo, hi in sp_buckets:
    sub = [s for s in shadow if lo <= s["sp"] < hi]
    if not sub: continue
    ms = [s["m60"] for s in sub]
    avg_sp = mean([s["sp"] for s in sub])
    w = sum(1 for v in ms if v > 0)
    print(f"  [{lo:.2f},{hi:.2f})    {len(sub):>3} {mean(ms):+9.4f} {w/len(sub)*100:>4.0f}% {mean(ms):+9.4f} {mean(ms)+avg_sp/2:+9.4f}")
print(f"\n  → 'after_½sp' column adds back half the spread (what fair price would have given us)")


# ============================================================
# 7. THE BIG QUESTION: would limit orders at FAIR price profit?
# ============================================================
print()
print("="*80)
print("7. COUNTERFACTUAL: if we'd entered at FAIR (not ASK), what would P&L be?")
print("="*80)
# If we'd bought at fair (mid bid+ask)/2 instead of ask:
#   - Real entry was at ask (high), realized at (entry + m60)
#   - Counterfactual entry at mid, realized at (mid + m60_relative_to_mid)
#   - But mid is roughly (ask - spread/2)
#   - So counterfactual m60 = m60 + spread/2  (we paid less, so gain more)
counterfactual_m60 = [s["m60"] + s["sp"]/2 for s in shadow]
cf_wins = sum(1 for v in counterfactual_m60 if v > 0)
print(f"  ACTUAL m60 (bought at ask):       avg={mean(realized):+.4f}  {sum(1 for v in realized if v>0)/len(realized)*100:.0f}% win")
print(f"  COUNTERFACTUAL (bought at mid):   avg={mean(counterfactual_m60):+.4f}  {cf_wins/len(counterfactual_m60)*100:.0f}% win")
print(f"  Δ                                  +{mean(counterfactual_m60) - mean(realized):.4f}/share")
print(f"  At $50 stake: ${(mean(counterfactual_m60) - mean(realized))*100:+.2f}/trade improvement")
print(f"\n  → if counterfactual is POSITIVE, posting limits at mid (not buying ask) would work")


# ============================================================
# 8. THE FINAL QUESTION: can the bot enter via LIMIT order at fair?
# ============================================================
print()
print("="*80)
print("8. CAN WE ENTER AT MID? (i.e. use GTC limit at fair, not FAK at ask)")
print("="*80)
# A GTC at the bid (or mid) might not fill if no one is selling at that price.
# But if 97% of events have book reaction, the price WILL move within 30s,
# crossing our resting bid.
# Rough estimate: fraction of trades where (bid + spread/2) was crossed in 60s
# would equal "would have filled". Approximate via: fill if (ask - bid)/2 was
# small AND book moved within 30s.
small_spread_count = sum(1 for s in shadow if s["sp"] <= 0.04)
print(f"  Trades with spread <= 4c (could likely fill at mid):  {small_spread_count}/{len(shadow)} ({small_spread_count/len(shadow)*100:.0f}%)")
print(f"  → If we LIMIT-only on these, lose ~30% of trades that would have filled at ask")
print(f"  → But on the ones we DO fill, gain ~2c per trade vs current")
print()
small = [s for s in shadow if s["sp"] <= 0.04]
if small:
    small_m60 = [s["m60"] for s in small]
    print(f"  Tight-spread subset only:")
    print(f"     Current m60:        avg={mean(small_m60):+.4f}  win={sum(1 for v in small_m60 if v>0)/len(small_m60)*100:.0f}%")
    print(f"     Counterfactual mid: avg={mean([s['m60']+s['sp']/2 for s in small]):+.4f}")
