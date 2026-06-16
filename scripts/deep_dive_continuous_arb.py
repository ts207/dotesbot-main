#!/usr/bin/env python3
"""Deep-dive analysis of continuous and arb on data_v2.

Sections:
  A. Continuous: per-tournament, per-phase, per-side, per-feature
  B. Continuous: failure modes (worst losers), concentration risk
  C. Continuous: sensitivity to gate-threshold changes
  D. Arb: opportunity timing, capital-utilization curve
  E. Arb: arb_cost distribution and per-tournament
  F. Arb: settlement-risk analysis (hold duration impact)
  G. Combined: capital usage profile over time, concurrency
  H. Stress: what if the new bot hits real-world friction (latency, fills)
"""
from __future__ import annotations

import bisect
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml
import pyarrow as pa
import pyarrow.dataset as pds
import pyarrow.compute as pc

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from continuous_scorer import score_snapshot, ContinuousSignal
from arb_scanner import scan_pair, ArbOpportunity, ArbReject

TOURNAMENT_NAMES = {
    "19696": "DreamLeague",
    "19101": "BLAST Slam",
    "19742": "tier-3",
}


def load_mapping():
    with open(REPO_ROOT / "markets.yaml") as f:
        mk = yaml.safe_load(f)
    return {m["dota_match_id"]: {
        "yes": m["yes_token_id"], "no": m["no_token_id"],
        "name": m.get("name", ""),
        "market_id": m.get("market_id", ""),
        "side": m.get("steam_side_mapping", "normal"),
    } for m in mk["markets"]
        if m.get("dota_match_id") and m["dota_match_id"].isdigit() and m["dota_match_id"]!="123"}


def load_snapshots():
    ds = pds.dataset(REPO_ROOT/"data_v2"/"snapshots", format="parquet", partitioning="hive")
    t = ds.to_table(columns=["match_id","received_at_ns","game_time_sec",
                              "radiant_lead","radiant_score","dire_score",
                              "data_source","league_id"],
                    filter=pc.field("data_source")=="top_live")
    snaps = defaultdict(list)
    league_of = {}
    for i in range(t.num_rows):
        mid = t["match_id"][i].as_py()
        league_of[mid] = t["league_id"][i].as_py()
        snaps[mid].append({
            "match_id": mid,
            "received_at_ns": t["received_at_ns"][i].as_py(),
            "game_time_sec": t["game_time_sec"][i].as_py(),
            "radiant_lead": t["radiant_lead"][i].as_py() or 0,
            "radiant_score": t["radiant_score"][i].as_py() or 0,
            "dire_score": t["dire_score"][i].as_py() or 0,
        })
    for mid in snaps: snaps[mid].sort(key=lambda x: x["received_at_ns"])
    return snaps, league_of


def load_book(tokens):
    ds = pds.dataset(REPO_ROOT/"data_v2"/"book_ticks", format="parquet", partitioning="hive")
    t = ds.to_table(columns=["asset_id","received_at_ns","best_bid","best_ask",
                              "mid","spread","ask_size","bid_size"],
                    filter=pc.is_in(pc.field("asset_id"), pa.array(list(tokens))))
    by = defaultdict(list)
    for i in range(t.num_rows):
        by[t["asset_id"][i].as_py()].append({
            "received_at_ns": t["received_at_ns"][i].as_py(),
            "best_bid": t["best_bid"][i].as_py(),
            "best_ask": t["best_ask"][i].as_py(),
            "mid": t["mid"][i].as_py(),
            "ask_size": t["ask_size"][i].as_py(),
            "bid_size": t["bid_size"][i].as_py(),
        })
    for a in by: by[a].sort(key=lambda x: x["received_at_ns"])
    return by


def book_at(book, asset_id, ns):
    arr = book.get(asset_id)
    if not arr: return None
    times = [x["received_at_ns"] for x in arr]
    i = bisect.bisect_right(times, ns) - 1
    if i < 0: return None
    return arr[i]


def book_with_mid(book):
    if book is None: return None
    bid = book.get("best_bid"); ask = book.get("best_ask"); mid = book.get("mid")
    if mid is None and bid is not None and ask is not None: mid = (bid + ask) / 2.0
    if mid is None: return None
    return {**book, "mid": mid}


# ---------- Continuous backtest with full feature capture ----------
def backtest_continuous_rich(snaps, book, mapping):
    """Run continuous; capture all features so we can slice afterward."""
    trades = []
    horizon = 60 * 1_000_000_000

    for mid in snaps:
        if mid not in mapping: continue
        info = mapping[mid]
        yes_tok, no_tok = info["yes"], info["no"]
        if yes_tok not in book or no_tok not in book: continue
        yes_arr = book[yes_tok]
        pregame_yes_mid = next((b["mid"] for b in yes_arr if b["mid"] is not None), 0.5)

        for i in range(1, len(snaps[mid])):
            prev_snap, cur_snap = snaps[mid][i-1], snaps[mid][i]
            yes_b = book_with_mid(book_at(book, yes_tok, cur_snap["received_at_ns"]))
            no_b  = book_with_mid(book_at(book, no_tok,  cur_snap["received_at_ns"]))
            if yes_b is None or no_b is None: continue
            res = score_snapshot(
                prev_snap=prev_snap, cur_snap=cur_snap,
                yes_book=yes_b, no_book=no_b,
                pregame_yes_mid=pregame_yes_mid,
                mapping={"steam_side_mapping": info["side"]},
            )
            if not isinstance(res, ContinuousSignal): continue
            target_tok = yes_tok if res.side == "YES" else no_tok
            entry_b = book_with_mid(book_at(book, target_tok, res.received_at_ns))
            exit_b  = book_with_mid(book_at(book, target_tok, res.received_at_ns + horizon))
            if entry_b is None or exit_b is None: continue
            entry_mid = entry_b["mid"]; exit_mid = exit_b["mid"]
            d_mid = exit_mid - entry_mid
            cost = 0.005
            ret_per_dollar = (d_mid - cost) / entry_mid
            pnl = ret_per_dollar * res.sized_usd
            trades.append({
                "signal_id": res.signal_id, "match_id": res.match_id,
                "ns": res.received_at_ns,
                "side": res.side, "direction": res.direction,
                "ref_mid_blended": res.ref_mid_blended,
                "game_time_sec": res.game_time_sec,
                "d_lead_1": res.d_lead_1, "d_kill_1": res.d_kill_1,
                "cur_lead_yes": res.cur_lead_yes,
                "pregame_signed": res.pregame_signed,
                "book_imbalance_yes": res.book_imbalance_yes,
                "book_imbalance_no": res.book_imbalance_no,
                "snap_gap_sec": res.snap_gap_sec,
                "conviction_mult": res.conviction_mult,
                "magnitude_mult": res.magnitude_mult,
                "sized_usd": res.sized_usd,
                "pnl": pnl, "entry_mid": entry_mid, "exit_mid": exit_mid,
                "d_mid": d_mid,
            })
    return trades


def backtest_arb_rich(mapping, book):
    trades = []
    for mid, info in mapping.items():
        yes_arr = book.get(info["yes"]); no_arr = book.get(info["no"])
        if not yes_arr or not no_arr: continue
        no_times = [n["received_at_ns"] for n in no_arr]
        match_end_ns = max(yes_arr[-1]["received_at_ns"], no_arr[-1]["received_at_ns"])
        had_arb = False
        for y in yes_arr:
            ns = y["received_at_ns"]
            if y["best_ask"] is None: continue
            i = bisect.bisect_right(no_times, ns) - 1
            if i < 0: continue
            n = no_arr[i]
            if abs(ns - n["received_at_ns"]) > 2_000_000_000: continue
            if n["best_ask"] is None: continue
            res = scan_pair(
                yes_book=y, no_book=n,
                mapping={"market_id": info["market_id"], "dota_match_id": mid,
                          "yes_token_id": info["yes"], "no_token_id": info["no"]},
                received_at_ns=ns,
                total_capital_usd=10.0,
                min_profit_cents=1.5,
            )
            if isinstance(res, ArbOpportunity):
                if had_arb: continue
                pnl = res.shares_per_side - res.total_capital_usd
                hold_sec = max((match_end_ns - ns) / 1e9, 0.1)
                trades.append({
                    "arb_id": res.arb_id, "match_id": mid, "ns": ns,
                    "yes_ask": res.yes_ask, "no_ask": res.no_ask,
                    "arb_cost": res.arb_cost, "profit_cents": res.profit_cents,
                    "shares": res.shares_per_side,
                    "total_capital_usd": res.total_capital_usd,
                    "pnl": pnl, "hold_sec": hold_sec,
                })
                had_arb = True
            elif isinstance(res, ArbReject) and had_arb and res.reason == "below_min_profit":
                had_arb = False
    return trades


def stats(pnls, label):
    if not pnls: return f"  {label}: 0"
    p = sorted(pnls); n = len(p); w = sum(1 for x in p if x > 0)
    return (f"  {label:<48} n={n:>4} ${sum(p):+8.2f} ${sum(p)/n:+.3f}/t "
            f"win={100*w/n:>3.0f}% med=${p[n//2]:+.3f}")


def main():
    print("Loading data_v2 ...")
    mapping = load_mapping()
    snaps, league_of = load_snapshots()
    yes_tokens = {info["yes"] for info in mapping.values()}
    no_tokens = {info["no"] for info in mapping.values()}
    book = load_book(yes_tokens | no_tokens)
    print(f"  {len(snaps)} matches, {len(book)} assets\n")

    print("Building rich continuous trade ledger ...")
    cont_trades = backtest_continuous_rich(snaps, book, mapping)
    print(f"  {len(cont_trades)} continuous trades\n")
    print("Building arb opportunity ledger ...")
    arb_trades = backtest_arb_rich(mapping, book)
    print(f"  {len(arb_trades)} arb opportunities\n")

    # =====================================================================
    # SECTION A: Continuous — slicing
    # =====================================================================
    print("=" * 78)
    print("A. CONTINUOUS — PnL slicing")
    print("=" * 78)

    print(f"\n  Overall: {stats([t['pnl'] for t in cont_trades], 'all continuous')[2:]}")

    print("\nA.1 Per-tournament:")
    by_t = defaultdict(list)
    for t in cont_trades:
        lg = league_of.get(t["match_id"], "0")
        name = TOURNAMENT_NAMES.get(str(lg), f"other ({lg})")
        by_t[name].append(t["pnl"])
    for name, pnls in sorted(by_t.items(), key=lambda x: -len(x[1])):
        print(stats(pnls, name))

    print("\nA.2 Per game-phase:")
    for lo, hi, lab in [(900,1200,"15-20m"),(1200,1500,"20-25m"),(1500,1800,"25-30m"),
                         (1800,2100,"30-35m"),(2100,2400,"35-40m"),(2400,2700,"40-45m")]:
        sub = [t['pnl'] for t in cont_trades if lo<=t['gt_minutes']<hi] if False else \
              [t['pnl'] for t in cont_trades if lo<=t['game_time_sec']<hi]
        print(stats(sub, f"{lab}"))

    print("\nA.3 Per side (YES vs NO trades):")
    for s in ["YES","NO"]:
        sub = [t['pnl'] for t in cont_trades if t["side"]==s]
        print(stats(sub, f"side={s}"))

    print("\nA.4 Per sizing multiplier combo:")
    by_mult = defaultdict(list)
    for t in cont_trades:
        key = f"conv={t['conviction_mult']:.1f}, mag={t['magnitude_mult']:.1f}, size=${t['sized_usd']:.2f}"
        by_mult[key].append(t['pnl'])
    for key, pnls in sorted(by_mult.items(), key=lambda x: -len(x[1])):
        print(stats(pnls, key))

    print("\nA.5 By |d_lead_1| magnitude:")
    for lo, hi, lab in [(1500,1800,"1500-1800"),(1800,2200,"1800-2200"),
                         (2200,2800,"2200-2800"),(2800,3500,"2800-3500"),
                         (3500,5000,"3500-5000"),(5000,99999,">=5000")]:
        sub = [t['pnl'] for t in cont_trades if lo<=abs(t['d_lead_1'])<hi]
        print(stats(sub, f"|Δlead|={lab}"))

    print("\nA.6 By ref_mid bucket:")
    for lo, hi in [(0.30,0.40),(0.40,0.50),(0.50,0.60),(0.60,0.70),(0.70,0.80),(0.80,0.85)]:
        sub = [t['pnl'] for t in cont_trades if lo<=t['ref_mid_blended']<hi]
        print(stats(sub, f"ref [{lo:.2f},{hi:.2f})"))

    # =====================================================================
    # SECTION B: Continuous — failure modes
    # =====================================================================
    print("\n" + "=" * 78)
    print("B. CONTINUOUS — failure modes")
    print("=" * 78)

    print("\nB.1 Worst 10 losers — what's the pattern?")
    losers = sorted(cont_trades, key=lambda t: t["pnl"])[:10]
    print(f"  {'pnl':>7} {'gt':>5} {'dlead':>6} {'cur_l':>7} {'side':<4} {'ref':>6} {'pregame':>8} {'mid_chg':>7}")
    for t in losers:
        print(f"  ${t['pnl']:+6.2f} {t['game_time_sec']:>5} {t['d_lead_1']:+6} "
              f"{t['cur_lead_yes']:+7} {t['side']:<4} {t['ref_mid_blended']:.3f} "
              f"{t['pregame_signed']:+8.3f} {t['d_mid']:+7.3f}")

    print("\nB.2 Concentration: which matches contribute most PnL?")
    by_match = defaultdict(list)
    for t in cont_trades: by_match[t["match_id"]].append(t["pnl"])
    match_totals = sorted([(m, sum(p), len(p)) for m, p in by_match.items()],
                           key=lambda x: -x[1])
    top_5 = match_totals[:5]; bot_5 = match_totals[-5:]
    print(f"  Top 5 winners:")
    for m, total, n in top_5:
        print(f"    {m}: ${total:+.2f} from {n} trades")
    print(f"  Bottom 5 losers:")
    for m, total, n in bot_5:
        print(f"    {m}: ${total:+.2f} from {n} trades")

    # Concentration of PnL
    sorted_totals = sorted([s for _,s,_ in match_totals], reverse=True)
    cum = 0; n_total = sum(t['pnl'] for t in cont_trades)
    top_10_pnl = sum(sorted_totals[:10])
    print(f"  Top 10 of {len(match_totals)} matches contribute ${top_10_pnl:+.2f}")
    print(f"  ({100*top_10_pnl/n_total:.0f}% of total PnL)")

    # =====================================================================
    # SECTION C: Continuous — sensitivity
    # =====================================================================
    print("\n" + "=" * 78)
    print("C. CONTINUOUS — sensitivity to gate thresholds")
    print("=" * 78)

    print("\nC.1 PnL by hold horizon (current setting: 60s):")
    horizons = [30, 60, 90, 120, 180]
    for h in horizons:
        h_ns = h * 1_000_000_000
        pnls = []
        for t in cont_trades:
            target_tok = mapping[t["match_id"]]["yes"] if t["side"]=="YES" else mapping[t["match_id"]]["no"]
            b = book_with_mid(book_at(book, target_tok, t["ns"] + h_ns))
            if b is None: continue
            d = b["mid"] - t["entry_mid"]
            pnls.append(((d - 0.005) / t["entry_mid"]) * t["sized_usd"])
        print(stats(pnls, f"hold={h}s"))

    print("\nC.2 What if we tighten conviction multiplier requirements (require BOTH conditions)?")
    sub_strict = [t['pnl'] for t in cont_trades if t['conviction_mult'] == 1.5]
    sub_loose  = [t['pnl'] for t in cont_trades if t['conviction_mult'] == 1.0]
    print(stats(sub_strict, "conviction met (1.5x sized)"))
    print(stats(sub_loose,  "conviction not met (1.0x sized)"))

    print("\nC.3 What if we drop the smallest-NW bucket [1500, 2000)?")
    keep_pnls = [t['pnl'] for t in cont_trades if abs(t['d_lead_1']) >= 2000]
    drop_pnls = [t['pnl'] for t in cont_trades if 1500 <= abs(t['d_lead_1']) < 2000]
    print(stats(keep_pnls, "kept (|Δlead|>=2000)"))
    print(stats(drop_pnls, "dropped (|Δlead|<2000)"))

    # =====================================================================
    # SECTION D: Arb — opportunity timing
    # =====================================================================
    print("\n" + "=" * 78)
    print("D. ARB — opportunity timing")
    print("=" * 78)

    print("\nD.1 Per-tournament arbs:")
    by_t = defaultdict(list)
    for t in arb_trades:
        lg = league_of.get(t["match_id"], "0")
        name = TOURNAMENT_NAMES.get(str(lg), f"other ({lg})")
        by_t[name].append(t["pnl"])
    for name, pnls in sorted(by_t.items(), key=lambda x: -len(x[1])):
        print(stats(pnls, name))

    print("\nD.2 Per arb_cost bucket (lower cost = better profit):")
    for lo, hi in [(0.80,0.85),(0.85,0.90),(0.90,0.93),(0.93,0.96),(0.96,0.99)]:
        sub = [t['pnl'] for t in arb_trades if lo<=t['arb_cost']<hi]
        print(stats(sub, f"arb_cost [{lo:.2f},{hi:.2f})"))

    print("\nD.3 Profit-cents distribution:")
    cents = sorted(t['profit_cents'] for t in arb_trades)
    if cents:
        n = len(cents)
        print(f"  min={cents[0]:.1f}c  p25={cents[n//4]:.1f}c  median={cents[n//2]:.1f}c  "
              f"p75={cents[3*n//4]:.1f}c  max={cents[-1]:.1f}c")
        for thresh in [2, 3, 5, 10]:
            count = sum(1 for c in cents if c >= thresh)
            print(f"  arbs with profit ≥ {thresh}c: {count}/{n} ({100*count/n:.0f}%)")

    # =====================================================================
    # SECTION E: Arb — hold duration / settlement
    # =====================================================================
    print("\n" + "=" * 78)
    print("E. ARB — hold duration and settlement risk")
    print("=" * 78)

    holds = sorted(t['hold_sec'] for t in arb_trades)
    if holds:
        n = len(holds)
        print(f"\n  Hold durations:")
        print(f"  min={holds[0]/60:.1f}m  p25={holds[n//4]/60:.1f}m  median={holds[n//2]/60:.1f}m  "
              f"p75={holds[3*n//4]/60:.1f}m  max={holds[-1]/60:.1f}m")
        for lo, hi in [(0,30*60),(30*60,90*60),(90*60,180*60),(180*60,360*60),(360*60,9999*60)]:
            sub = [t for t in arb_trades if lo<=t['hold_sec']<hi]
            if not sub: continue
            label = f"hold {lo//60:>3}-{hi//60:>4}min"
            print(stats([t['pnl'] for t in sub], label))

    # =====================================================================
    # SECTION F: Combined capital utilization
    # =====================================================================
    print("\n" + "=" * 78)
    print("F. CAPITAL UTILIZATION — combined continuous + arb over time")
    print("=" * 78)

    # Build event timeline
    events = []
    for t in cont_trades:
        events.append((t['ns'], 'cont_in', t['sized_usd']))
        events.append((t['ns'] + 60*1_000_000_000, 'cont_out', -t['sized_usd']))
    for t in arb_trades:
        events.append((t['ns'], 'arb_in', t['total_capital_usd']))
        events.append((t['ns'] + int(t['hold_sec']*1e9), 'arb_out', -t['total_capital_usd']))
    events.sort()

    peak = 0
    cur = 0
    peak_at_ns = 0
    cur_max_history = []
    for ns, kind, dollars in events:
        cur += dollars
        cur_max_history.append((ns, cur))
        if cur > peak:
            peak = cur; peak_at_ns = ns

    print(f"\n  Peak concurrent capital deployed: ${peak:.2f}")
    if peak_at_ns:
        peak_dt = datetime.fromtimestamp(peak_at_ns/1e9, tz=timezone.utc)
        print(f"  Peak occurred at: {peak_dt.isoformat()}")

    # Avg capital
    if len(cur_max_history) >= 2:
        weighted_sum = 0
        for i in range(1, len(cur_max_history)):
            dt_sec = (cur_max_history[i][0] - cur_max_history[i-1][0]) / 1e9
            weighted_sum += cur_max_history[i-1][1] * dt_sec
        total_window_sec = (cur_max_history[-1][0] - cur_max_history[0][0]) / 1e9
        avg_capital = weighted_sum / total_window_sec if total_window_sec > 0 else 0
        print(f"  Time-weighted avg capital deployed: ${avg_capital:.2f}")

    # =====================================================================
    # SECTION G: Stress — concurrency and real-world friction
    # =====================================================================
    print("\n" + "=" * 78)
    print("G. STRESS — concurrent positions / would-the-bot-handle-it")
    print("=" * 78)

    # Build per-second concurrent count for continuous (60s holds)
    cont_intervals = [(t['ns'], t['ns'] + 60*1_000_000_000) for t in cont_trades]
    arb_intervals = [(t['ns'], t['ns'] + int(t['hold_sec']*1e9)) for t in arb_trades]

    # Sample concurrent positions at each trade's entry
    cont_max_concurrent = 0
    for t in cont_trades:
        c = sum(1 for s,e in cont_intervals if s <= t['ns'] <= e)
        if c > cont_max_concurrent: cont_max_concurrent = c

    arb_max_concurrent = 0
    for t in arb_trades:
        c = sum(1 for s,e in arb_intervals if s <= t['ns'] <= e)
        if c > arb_max_concurrent: arb_max_concurrent = c

    print(f"\n  Continuous max concurrent positions: {cont_max_concurrent}")
    print(f"  Arb max concurrent positions:        {arb_max_concurrent}")
    print(f"  Configured caps: CONTINUOUS_MAX_OPEN_POSITIONS=5, ARB_MAX_OPEN_POSITIONS=5")

    if cont_max_concurrent > 5:
        print(f"  ⚠️  Continuous max ({cont_max_concurrent}) exceeds cap (5) — some signals would be dropped live")
    if arb_max_concurrent > 5:
        print(f"  ⚠️  Arb max ({arb_max_concurrent}) exceeds cap (5) — some opportunities would be skipped")

    # Robustness check: what if we only execute the FIRST signal per match (no
    # multi-trade-per-match)?
    print("\nG.1 What if we cap continuous to 1 trade per match?")
    seen = set(); kept = []
    for t in sorted(cont_trades, key=lambda x: x['ns']):
        if t['match_id'] in seen: continue
        seen.add(t['match_id']); kept.append(t['pnl'])
    print(stats(kept, "1 trade/match max"))

    print("\nG.2 Realistic execution friction (1c half-spread instead of 0.5c):")
    pnls_1c = []
    for t in cont_trades:
        d_mid = t['exit_mid'] - t['entry_mid']
        signed_d = d_mid if t['direction'] > 0 else -d_mid
        # Direction already baked into d_mid via side; recompute cleanly:
        ret = (d_mid - 0.010) / t['entry_mid']
        pnls_1c.append(ret * t['sized_usd'])
    print(stats(pnls_1c, "continuous at 1c half-cost"))

    # Arb at 1c slippage on each leg (very conservative)
    pnls_arb_1c = []
    for t in arb_trades:
        shares = t['shares']
        # Pretend we paid 1c more per share on each leg (slippage)
        pnl = shares - t['total_capital_usd'] - 2 * shares * 0.01
        pnls_arb_1c.append(pnl)
    print(stats(pnls_arb_1c, "arb at 1c slippage per leg"))


if __name__ == "__main__":
    main()
