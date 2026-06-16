"""DEEP RAW DATA ANALYSIS — cross-references all bot logs to find what works.

Mines:
  raw_snapshots.csv     — game state (game_time, lead, scores, tower_state)
  dota_events.csv       — events detected
  book_events.csv       — Polymarket order book changes
  shadow_trades.csv     — paper trades with markouts
  scalp_trades.csv      — Dota scalp paper closes
  lol_scalp_paper.csv   — LoL scalp paper closes (if exists)
  live_attempts.csv     — real CLOB attempts
  live_exits.csv        — exit lifecycle

Outputs:
  1. Match coverage — of all live matches, how many had any signal
  2. Event-to-book-move correlation — when event fires, does price move within N s
  3. Win rate by entry timing (early/mid/late game)
  4. Loss concentration analysis — power-law in match losses
  5. Scalp performance comparison — Dota vs LoL paper
  6. Per-token order book volatility — which markets actually move
  7. Cumulative P&L curve (paper) — drawdown profile
"""
from __future__ import annotations
import csv
import json
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, stdev

ROOT = Path('/home/tstuv/dota-poly-signal-pnl-asd')


def fnum(s):
    try: return float(s) if s not in ("", None) else None
    except: return None


def parse_iso(s: str):
    try: return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except: return None


def section(title: str):
    print(f"\n{'='*80}\n{title}\n{'='*80}")


# ============================================================
# Load everything
# ============================================================
section("LOADING DATA")

# Raw snapshots
snaps = []
with (ROOT/"logs/raw_snapshots.csv").open() as f:
    for r in csv.DictReader(f):
        ts = parse_iso(r["received_at_utc"])
        if not ts: continue
        snaps.append({
            "ts": ts, "match_id": r["match_id"], "league_id": r.get("league_id"),
            "gt": int(float(r.get("game_time_sec") or 0)),
            "lead": int(float(r.get("radiant_lead") or 0)),
            "r_score": int(float(r.get("radiant_score") or 0)),
            "d_score": int(float(r.get("dire_score") or 0)),
            "go": str(r.get("game_over","")).lower() in ("true","1","yes"),
        })
print(f"  raw_snapshots:  {len(snaps):>7} rows")

# Events
events = []
with (ROOT/"logs/dota_events.csv").open() as f:
    for r in csv.DictReader(f):
        ts = parse_iso(r["timestamp_utc"])
        if not ts: continue
        events.append({
            "ts": ts, "match_id": r["match_id"], "event": r["event_type"],
            "direction": r.get("direction"), "tier": r.get("event_tier"),
            "gt": int(float(r.get("game_time_sec") or 0)),
            "lead": int(float(r.get("radiant_lead") or 0)),
            "kd": int(float(r.get("kill_diff_delta") or 0)),
            "nw": int(float(r.get("networth_delta") or 0)),
        })
print(f"  dota_events:    {len(events):>7} rows")

# Book events (sample only — file is huge)
book_ticks_by_asset = defaultdict(list)
with (ROOT/"logs/book_events.csv").open() as f:
    for r in csv.DictReader(f):
        ts = parse_iso(r["timestamp_utc"])
        if not ts: continue
        aid = r.get("asset_id", "")
        bid = fnum(r.get("best_bid")); ask = fnum(r.get("best_ask"))
        if not aid or bid is None and ask is None: continue
        book_ticks_by_asset[aid].append((ts, bid, ask))
print(f"  book_events:    {sum(len(v) for v in book_ticks_by_asset.values()):>7} ticks across {len(book_ticks_by_asset)} assets")

# Shadow trades
shadow = []
with (ROOT/"logs/shadow_trades.csv").open() as f:
    for r in csv.DictReader(f):
        if r.get("decision") != "paper_buy_yes": continue
        try:
            shadow.append({
                "ts": parse_iso(r["timestamp_utc"]),
                "match_id": r["match_id"], "event": r["event_type"],
                "ep": float(r["entry_price"]), "sp": float(r.get("spread_at_entry") or 0),
                "m60": float(r["markout_60s"]),
                "gt": float(r.get("game_time_sec") or 0),
                "side": r.get("side"),
            })
        except (ValueError, TypeError, KeyError): continue
print(f"  shadow_trades:  {len(shadow):>7} paper_buys")

# Scalp pairs (Dota)
scalp_dota = []
sp = ROOT/"logs/scalp_trades.csv"
if sp.exists():
    with sp.open() as f:
        for r in csv.DictReader(f):
            try:
                scalp_dota.append({
                    "ts": r.get("timestamp_utc",""),
                    "market": r.get("market_id"), "match": r.get("match_id"),
                    "yes_e": float(r.get("yes_entry_px",0) or 0),
                    "no_e": float(r.get("no_entry_px",0) or 0),
                    "pnl": float(r.get("total_pnl_usd",0) or 0),
                    "reason": r.get("close_reason",""),
                })
            except (ValueError, TypeError): continue
print(f"  scalp_trades:   {len(scalp_dota):>7} Dota pairs")

# LoL scalp paper
scalp_lol = []
lp = ROOT/"logs/lol_scalp_paper.csv"
if lp.exists():
    with lp.open() as f:
        for r in csv.DictReader(f):
            try:
                scalp_lol.append({
                    "ts": r.get("closed_at_utc",""),
                    "market": r.get("market_id"), "q": r.get("question",""),
                    "yes_e": float(r.get("yes_entry_px",0) or 0),
                    "no_e": float(r.get("no_entry_px",0) or 0),
                    "pnl": float(r.get("total_pnl_usd",0) or 0),
                    "reason": r.get("close_reason",""),
                })
            except (ValueError, TypeError): continue
print(f"  lol_scalp_paper:{len(scalp_lol):>7} LoL pairs")


# ============================================================
# 1. MATCH COVERAGE: of all live matches, how many had signals + trades?
# ============================================================
section("1. MATCH COVERAGE — funnel from live matches to trades")

snap_matches = {s["match_id"] for s in snaps}
event_matches = {e["match_id"] for e in events}
trade_matches = {s["match_id"] for s in shadow}

print(f"  Matches observed (snapshots):  {len(snap_matches):>5}")
print(f"  Matches with events fired:     {len(event_matches):>5}  ({len(event_matches)/max(len(snap_matches),1)*100:.0f}%)")
print(f"  Matches with paper trades:     {len(trade_matches):>5}  ({len(trade_matches)/max(len(snap_matches),1)*100:.0f}%)")
print(f"  Matches with Dota scalp pairs: {len(set(p['match'] for p in scalp_dota if p['match'])):>5}")

# Of matches with events, how many had snapshots both before AND after the event?
proper_coverage = 0
for m in event_matches:
    m_snaps = [s for s in snaps if s["match_id"] == m]
    m_evs = [e for e in events if e["match_id"] == m]
    if not m_snaps or not m_evs: continue
    has_before = any(s["ts"] < m_evs[0]["ts"] for s in m_snaps)
    has_after = any(s["ts"] > m_evs[-1]["ts"] for s in m_snaps)
    if has_before and has_after: proper_coverage += 1
print(f"  Matches with full event-window snapshot coverage: {proper_coverage}/{len(event_matches)}")


# ============================================================
# 2. EVENT → BOOK MOVE correlation
# ============================================================
section("2. EVENT → BOOK MOVE — does book react when event fires?")
# For each event, find the corresponding token (need market mapping). Without
# perfect mapping, fall back to a simpler check: when ANY event fires, do
# any book updates happen within 30s? (rough proxy for market sensitivity.)

# Index book ticks by 5s buckets
book_by_5s = defaultdict(int)
for aid, ticks in book_ticks_by_asset.items():
    for ts, _, _ in ticks:
        bucket = int(ts.timestamp()) // 5
        book_by_5s[bucket] += 1

reacted = inactive = 0
for ev in events:
    bucket_now = int(ev["ts"].timestamp()) // 5
    # Did book update in next 6 buckets (30s)?
    moved = sum(book_by_5s.get(bucket_now + i, 0) for i in range(0, 7))
    if moved > 0: reacted += 1
    else: inactive += 1
print(f"  Events fired:                    {len(events)}")
print(f"  Events with book reaction in 30s:  {reacted}  ({reacted/max(len(events),1)*100:.0f}%)")
print(f"  Events with NO book reaction:      {inactive}  ({inactive/max(len(events),1)*100:.0f}%)")
print(f"  → high reaction = signals catching real market moves")


# ============================================================
# 3. ENTRY TIMING (game phase)
# ============================================================
section("3. ENTRY TIMING — shadow trades by game phase")
phases = [(0,600,"early <10m"), (600,1500,"mid 10-25m"), (1500,2400,"late 25-40m"), (2400,9999,"vlate >40m")]
print(f"  {'phase':>15s} {'n':>4s} {'win%':>5s} {'avg_m60':>9s}")
for lo, hi, lbl in phases:
    sub = [s for s in shadow if lo <= s["gt"] < hi]
    if not sub: continue
    ms = [s["m60"] for s in sub]
    w = sum(1 for v in ms if v > 0)
    print(f"  {lbl:>15s} {len(sub):>4} {w/len(sub)*100:>4.0f}% {mean(ms):+9.4f}")


# ============================================================
# 4. LOSS CONCENTRATION (per-match P&L distribution)
# ============================================================
section("4. LOSS CONCENTRATION — is it power-law?")
by_match = defaultdict(list)
for s in shadow: by_match[s["match_id"]].append(s["m60"])
pnls = sorted([(m, sum(ms), len(ms)) for m, ms in by_match.items()], key=lambda x: x[1])
total = sum(p for _, p, _ in pnls)
print(f"  Matches with trades:  {len(pnls)}  Total P&L: {total:+.3f}")
print(f"\n  Worst 5:")
for m, p, n_ in pnls[:5]:
    print(f"    {m:>12s} n={n_:>2} {p:+.3f}")
print(f"\n  Best 5:")
for m, p, n_ in pnls[-5:]:
    print(f"    {m:>12s} n={n_:>2} {p:+.3f}")
worst_5_sum = sum(p for _, p, _ in pnls[:5])
best_5_sum = sum(p for _, p, _ in pnls[-5:])
print(f"\n  Worst 5 matches = {worst_5_sum:+.3f}  ({worst_5_sum/abs(total)*100 if total else 0:.0f}% of |total|)")
print(f"  Best 5 matches  = {best_5_sum:+.3f}  ({best_5_sum/abs(total)*100 if total else 0:.0f}% of |total|)")


# ============================================================
# 5. SCALP vs SHADOW comparison
# ============================================================
section("5. SCALP vs SHADOW comparison")
shadow_pnl = sum(s["m60"] for s in shadow) * 100  # ~$/share×100 ≈ $/$100stake
shadow_wins = sum(1 for s in shadow if s["m60"] > 0)
dota_scalp_pnl = sum(p["pnl"] for p in scalp_dota)
dota_scalp_wins = sum(1 for p in scalp_dota if p["pnl"] > 0)
lol_scalp_pnl = sum(p["pnl"] for p in scalp_lol)
lol_scalp_wins = sum(1 for p in scalp_lol if p["pnl"] > 0)
print(f"  {'strategy':>25s} {'n':>3s} {'win%':>5s} {'total_$':>10s} {'avg/trade':>10s}")
print(f"  {'SHADOW events (paper)':>25s} {len(shadow):>3} {shadow_wins/max(len(shadow),1)*100:>4.0f}% ${shadow_pnl:>+9.0f} ${shadow_pnl/max(len(shadow),1):>+9.2f}")
print(f"  {'DOTA scalp pairs':>25s} {len(scalp_dota):>3} {dota_scalp_wins/max(len(scalp_dota),1)*100:>4.0f}% ${dota_scalp_pnl:>+9.0f} ${dota_scalp_pnl/max(len(scalp_dota),1):>+9.2f}")
print(f"  {'LoL scalp pairs':>25s} {len(scalp_lol):>3} {lol_scalp_wins/max(len(scalp_lol),1)*100:>4.0f}% ${lol_scalp_pnl:>+9.0f} ${lol_scalp_pnl/max(len(scalp_lol),1):>+9.2f}")


# ============================================================
# 6. BOOK VOLATILITY by asset
# ============================================================
section("6. BOOK VOLATILITY per asset (which markets actually move)")
asset_moves = []
for aid, ticks in book_ticks_by_asset.items():
    if len(ticks) < 10: continue
    bids = [b for _, b, _ in ticks if b is not None]
    if len(bids) < 5: continue
    bid_range = max(bids) - min(bids)
    asset_moves.append((aid, len(ticks), bid_range, mean(bids)))
asset_moves.sort(key=lambda x: -x[2])
print(f"  Top 10 most-volatile assets (largest bid range):")
print(f"  {'asset':>15s} {'ticks':>6s} {'bid_range':>10s} {'avg_bid':>8s}")
for aid, n_t, r, avg in asset_moves[:10]:
    print(f"  ...{aid[-12:]} {n_t:>6} {r:>10.3f} {avg:>8.3f}")
mean_range = mean([r for _, _, r, _ in asset_moves]) if asset_moves else 0
print(f"\n  Mean bid range across all assets: {mean_range:.3f}")
print(f"  → high range = more scalp opportunity")


# ============================================================
# 7. CUMULATIVE P&L CURVE (drawdown profile)
# ============================================================
section("7. CUMULATIVE SHADOW P&L (drawdown profile)")
sorted_shadow = sorted(shadow, key=lambda s: s["ts"])
cum = 0.0; peak = 0.0; max_dd = 0.0
samples = []
for i, s in enumerate(sorted_shadow):
    cum += s["m60"]
    if cum > peak: peak = cum
    dd = peak - cum
    if dd > max_dd: max_dd = dd
    if i % max(1, len(sorted_shadow)//10) == 0:
        samples.append((s["ts"], cum, dd))
samples.append((sorted_shadow[-1]["ts"] if sorted_shadow else None, cum, peak - cum))
print(f"  Trades over time (sampled):")
print(f"  {'timestamp':>22s} {'cum_pnl':>9s} {'drawdown':>9s}")
for ts, c, d in samples:
    if ts: print(f"  {ts.isoformat()[:19]:>22s} {c:>+9.3f} {d:>+9.3f}")
print(f"\n  Final cumulative P&L:  {cum:+.3f}")
print(f"  Max drawdown:          {max_dd:+.3f}")
print(f"  Peak P&L:              {peak:+.3f}")
print(f"  At $50/trade ($/$1 ≈ 100 shares):")
print(f"     Final $:  ${cum*100:+.0f}")
print(f"     Max DD:   ${max_dd*100:+.0f}")


# ============================================================
# 8. KEY TAKEAWAYS
# ============================================================
section("8. CROSS-LOG TAKEAWAYS")
print(f"""
  Data volume today/recent:
    {len(snaps):>7} game-state snapshots
    {len(events):>7} signal events detected
    {len(shadow):>7} paper trades (markout-validated)
    {len(scalp_dota):>3} Dota scalp pairs closed
    {len(scalp_lol):>3} LoL scalp pairs closed

  Pipeline funnel:
    {len(snap_matches):>3} matches snapshotted
    → {len(event_matches):>3} matches generated events ({len(event_matches)/max(len(snap_matches),1)*100:.0f}%)
    → {len(trade_matches):>3} matches reached paper trade ({len(trade_matches)/max(len(snap_matches),1)*100:.0f}%)

  Strategy P&L on shadow data (at $50 stake):
    EVENT strategy:    ${shadow_pnl:+.0f}  ({shadow_wins}/{len(shadow)} = {shadow_wins/max(len(shadow),1)*100:.0f}% win)
    DOTA scalp:        ${dota_scalp_pnl:+.0f}  ({dota_scalp_wins}/{len(scalp_dota)} = {dota_scalp_wins/max(len(scalp_dota),1)*100:.0f}% win)
    LoL scalp:         ${lol_scalp_pnl:+.0f}  ({lol_scalp_wins}/{len(scalp_lol)} = {lol_scalp_wins/max(len(scalp_lol),1)*100:.0f}% win)

  Single biggest loss: ${worst_5_sum*100:+.0f} from 5 matches
  Single biggest win:  ${best_5_sum*100:+.0f} from 5 matches
""")
