#!/usr/bin/env python3
"""Strategy investigation on data_v2.

Builds a feature-rich trade ledger for POLL_FIGHT_SWING + ref-band gate,
then runs seven analyses:
    1. PREMIUM tier definitions
    2. Exit timing optimization (5/10/20/30/60/120s holds)
    3. Spread-band gate validation
    4. Phase fine-tuning (45-50m, >60m skips)
    5. Multi-trade-per-match correlation
    6. Edge-weighted sizing
    7. Cooldown / second-signal-on-match

Output: one report block per analysis with a concrete recommendation.
"""
from __future__ import annotations

import bisect
import sys
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import yaml
import pyarrow as pa
import pyarrow.dataset as pds
import pyarrow.compute as pc

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

NOTIONAL = 5.0
NORMAL_GAP_NS = 75 * 1_000_000_000
COST_HALF_SPREAD = 0.005
REF_LOW, REF_HIGH = 0.30, 0.85


# ---------- loaders ----------
def load_mapping():
    with open(REPO_ROOT / "markets.yaml") as f:
        mk = yaml.safe_load(f)
    return {m["dota_match_id"]: {"yes": m["yes_token_id"], "side": m.get("steam_side_mapping","normal")}
            for m in mk["markets"]
            if m.get("dota_match_id") and m["dota_match_id"].isdigit() and m["dota_match_id"] != "123"}


def load_snapshots():
    ds = pds.dataset(REPO_ROOT / "data_v2" / "snapshots", format="parquet", partitioning="hive")
    t = ds.to_table(columns=["match_id","received_at_ns","game_time_sec",
                              "radiant_lead","radiant_score","dire_score","data_source"],
                    filter=pc.field("data_source")=="top_live")
    snaps = defaultdict(list)
    for i in range(t.num_rows):
        snaps[t["match_id"][i].as_py()].append({
            "ns": t["received_at_ns"][i].as_py(),
            "gt": t["game_time_sec"][i].as_py(),
            "rl": t["radiant_lead"][i].as_py() or 0,
            "rs": t["radiant_score"][i].as_py() or 0,
            "ds": t["dire_score"][i].as_py() or 0,
        })
    for mid in snaps: snaps[mid].sort(key=lambda x: x["ns"])
    return snaps


def load_book(tokens):
    ds = pds.dataset(REPO_ROOT / "data_v2" / "book_ticks", format="parquet", partitioning="hive")
    t = ds.to_table(columns=["asset_id","received_at_ns","mid","spread"],
                    filter=pc.is_in(pc.field("asset_id"), pa.array(list(tokens))))
    by_asset = defaultdict(list)
    for i in range(t.num_rows):
        m = t["mid"][i].as_py()
        if m is None: continue
        by_asset[t["asset_id"][i].as_py()].append((t["received_at_ns"][i].as_py(), m, t["spread"][i].as_py()))
    for a in by_asset: by_asset[a].sort()
    return {a: (
        [x[0] for x in by_asset[a]],
        [x[1] for x in by_asset[a]],
        [x[2] for x in by_asset[a]],
    ) for a in by_asset}


def book_at(book, asset_id, ns):
    arr = book.get(asset_id)
    if not arr: return None, None
    times, mids, spreads = arr
    i = bisect.bisect_right(times, ns) - 1
    if i < 0: return None, None
    return mids[i], spreads[i]


def fs_fires(prev, cur):
    if cur["ns"] - prev["ns"] > NORMAL_GAP_NS: return False
    dl = cur["rl"] - prev["rl"]
    dk = (cur["rs"] - prev["rs"]) - (cur["ds"] - prev["ds"])
    if abs(dl) < 1500 or abs(dk) < 1: return False
    if (dl > 0) != (dk > 0): return False
    return True


# ---------- build trade ledger ----------
def build_ledger():
    m2info = load_mapping()
    snaps = load_snapshots()
    joinable = [mid for mid in snaps if mid in m2info]
    tokens = {m2info[mid]["yes"] for mid in joinable}
    book = load_book(tokens)
    joined_mids = [mid for mid in joinable if m2info[mid]["yes"] in book]

    horizons = [5, 10, 20, 30, 60, 120]
    trades = []
    for mid in joined_mids:
        info = m2info[mid]
        sign = 1 if info["side"]=="normal" else -1
        yes_tok = info["yes"]
        srows = snaps[mid]
        for i in range(1, len(srows)):
            prev, cur = srows[i-1], srows[i]
            if cur["gt"] is None or cur["gt"] < 300: continue
            if not fs_fires(prev, cur): continue
            dl_yes = (cur["rl"] - prev["rl"]) * sign
            direction = 1 if dl_yes > 0 else (-1 if dl_yes < 0 else 0)
            if direction == 0: continue
            mid0, spread0 = book_at(book, yes_tok, cur["ns"])
            if mid0 is None: continue
            if not (REF_LOW <= mid0 <= REF_HIGH): continue
            # Markouts at multiple horizons
            mks = {}
            for h in horizons:
                m_h, _ = book_at(book, yes_tok, cur["ns"] + h * 1_000_000_000)
                mks[h] = (m_h - mid0) if m_h is not None else None
            if mks[30] is None: continue  # require canonical horizon

            cur_lead_yes = cur["rl"] * sign
            trades.append({
                "mid": mid, "ns": cur["ns"], "gt": cur["gt"],
                "d_lead_raw": cur["rl"] - prev["rl"],   # raw radiant_lead delta
                "d_lead_yes": dl_yes,
                "cur_lead_yes": cur_lead_yes,
                "direction": direction,
                "ref": mid0, "spread": spread0,
                "markouts": mks,
                "yes_tok": yes_tok,
            })
    return trades, book


def pnl_for(t, horizon, cost=COST_HALF_SPREAD):
    dm = t["markouts"].get(horizon)
    if dm is None: return None
    return ((dm * t["direction"] - cost) / t["ref"]) * NOTIONAL


def stats(pnls, label=""):
    if not pnls: return f"{label}: 0"
    pnls = sorted(pnls)
    n=len(pnls); w=sum(1 for p in pnls if p>0)
    return (f"n={n:>4} total=${sum(pnls):+.2f} ${sum(pnls)/n:+.3f}/t win={100*w/n:>4.0f}% "
            f"med=${pnls[n//2]:+.3f} p25=${pnls[n//4]:+.3f} p75=${pnls[3*n//4]:+.3f}")


def classify(t):
    fav = t["direction"]
    lead_for_fav = t["cur_lead_yes"] * fav
    if abs(lead_for_fav) < 1000: return "close_game"
    if lead_for_fav > 0: return "lead_extension"
    return "comeback_against"


# ---------- analyses ----------
def analysis_1_premium(trades):
    print("\n" + "="*78)
    print("ANALYSIS #1: PREMIUM tier definitions (sized 2× when triggered)")
    print("="*78)
    pnls_all = [pnl_for(t, 30) for t in trades]
    pnls_all = [p for p in pnls_all if p is not None]
    print(f"Baseline (all FS + ref-band, 30s):  {stats(pnls_all)}")

    variants = [
        ("V1: lead_extension only",
         lambda t: classify(t)=="lead_extension"),
        ("V2: V1 + |Δ| in [1500, 3500)",
         lambda t: classify(t)=="lead_extension" and 1500<=abs(t["d_lead_raw"])<3500),
        ("V3: V2 + gt>=1800",
         lambda t: classify(t)=="lead_extension" and 1500<=abs(t["d_lead_raw"])<3500 and t["gt"]>=1800),
        ("V4: V2 + (gt in [900,1200] or gt>=1800)",
         lambda t: classify(t)=="lead_extension" and 1500<=abs(t["d_lead_raw"])<3500 and ((900<=t["gt"]<1200) or t["gt"]>=1800)),
        ("V5: V4 OR (close_game + |Δ| in [1500,3500) + 15-40m)",
         lambda t: (classify(t)=="lead_extension" and 1500<=abs(t["d_lead_raw"])<3500 and ((900<=t["gt"]<1200) or t["gt"]>=1800))
                   or (classify(t)=="close_game" and 1500<=abs(t["d_lead_raw"])<3500 and 900<=t["gt"]<2400)),
        ("V6: ref in [0.40, 0.70) + lead_extension",
         lambda t: classify(t)=="lead_extension" and 0.40<=t["ref"]<0.70),
        ("V7: gt in [900,1200] or [1800,2100] or [3000,3600] (strong-phase only)",
         lambda t: (900<=t["gt"]<1200) or (1800<=t["gt"]<2100) or (3000<=t["gt"]<3600)),
    ]
    print()
    for label, fn in variants:
        sub = [pnl_for(t, 30) for t in trades if fn(t)]
        sub = [p for p in sub if p is not None]
        print(f"  {label:<55} {stats(sub)}")


def analysis_2_exit_timing(trades):
    print("\n" + "="*78)
    print("ANALYSIS #2: Exit timing (hold length)")
    print("="*78)
    for h in [5, 10, 20, 30, 60, 120]:
        pnls = [pnl_for(t, h) for t in trades]
        pnls = [p for p in pnls if p is not None]
        print(f"  hold {h:>3}s: {stats(pnls)}")
    print()
    # Now by class
    for cls in ["lead_extension", "comeback_against", "close_game"]:
        print(f"\n  {cls}:")
        for h in [10, 30, 60, 120]:
            pnls = [pnl_for(t, h) for t in trades if classify(t)==cls]
            pnls = [p for p in pnls if p is not None]
            if pnls:
                print(f"    hold {h:>3}s: {stats(pnls)}")


def analysis_3_spread(trades):
    print("\n" + "="*78)
    print("ANALYSIS #3: Spread-band gate validation")
    print("="*78)
    buckets = [(0,0.01),(0.01,0.02),(0.02,0.03),(0.03,0.05),(0.05,0.10),(0.10,0.20)]
    for lo, hi in buckets:
        sub = [pnl_for(t, 30) for t in trades if t["spread"] is not None and lo<=t["spread"]<hi]
        sub = [p for p in sub if p is not None]
        print(f"  spread [{lo:.2f}, {hi:.2f}):  {stats(sub)}")

    # Test the toxic 2-5c skip
    keep = [pnl_for(t, 30) for t in trades if t["spread"] is None or not (0.02 <= t["spread"] < 0.05)]
    keep = [p for p in keep if p is not None]
    print(f"\n  Skip 2-5c spread band:        {stats(keep)}")
    all_p = [pnl_for(t, 30) for t in trades]
    all_p = [p for p in all_p if p is not None]
    print(f"  No spread filter (baseline):  {stats(all_p)}")


def analysis_4_phase(trades):
    print("\n" + "="*78)
    print("ANALYSIS #4: Phase fine-tuning")
    print("="*78)
    buckets = [(0,900,"<15m"),(900,1200,"15-20m"),(1200,1500,"20-25m"),(1500,1800,"25-30m"),
               (1800,2100,"30-35m"),(2100,2400,"35-40m"),(2400,2700,"40-45m"),(2700,3000,"45-50m"),
               (3000,3300,"50-55m"),(3300,3600,"55-60m"),(3600,4500,"60-75m"),(4500,99999,">75m")]
    for lo, hi, lab in buckets:
        sub = [pnl_for(t, 30) for t in trades if lo<=t["gt"]<hi]
        sub = [p for p in sub if p is not None]
        if len(sub) >= 5:
            print(f"  {lab:<10} {stats(sub)}")
    # Variants
    print()
    drops = [
        ("Drop 45-50m AND >60m",
         lambda t: not ((2700<=t["gt"]<3000) or t["gt"]>=3600)),
        ("Drop <15m AND 45-50m AND >60m",
         lambda t: t["gt"]>=900 and not (2700<=t["gt"]<3000) and t["gt"]<3600),
        ("Keep only [900,1200), [1800,2100), [2400,2700), [3000,3300)",
         lambda t: (900<=t["gt"]<1200) or (1800<=t["gt"]<2100) or (2400<=t["gt"]<2700) or (3000<=t["gt"]<3300)),
    ]
    base = [pnl_for(t, 30) for t in trades]
    base = [p for p in base if p is not None]
    print(f"  Baseline:                {stats(base)}")
    for label, fn in drops:
        sub = [pnl_for(t, 30) for t in trades if fn(t)]
        sub = [p for p in sub if p is not None]
        print(f"  {label:<55} {stats(sub)}")


def analysis_5_multi_trade(trades):
    print("\n" + "="*78)
    print("ANALYSIS #5: Multi-trade-per-match correlation")
    print("="*78)
    by_match = defaultdict(list)
    for t in trades:
        by_match[t["mid"]].append(t)
    n_per_match = Counter(len(v) for v in by_match.values())
    print(f"Trades-per-match distribution: {dict(sorted(n_per_match.items()))}")

    # Rank trades within each match (1st, 2nd, 3rd, ...) and compute pnl by rank
    ranks = defaultdict(list)
    for trs in by_match.values():
        trs_sorted = sorted(trs, key=lambda x: x["ns"])
        for rank, tr in enumerate(trs_sorted, start=1):
            p = pnl_for(tr, 30)
            if p is not None:
                ranks[rank].append(p)
    print()
    for rank in sorted(ranks):
        if len(ranks[rank]) >= 5:
            print(f"  trade #{rank} in a match: {stats(ranks[rank])}")

    # Caps
    print()
    for cap in [1, 2, 3]:
        kept = []
        for trs in by_match.values():
            for tr in sorted(trs, key=lambda x: x["ns"])[:cap]:
                p = pnl_for(tr, 30)
                if p is not None: kept.append(p)
        print(f"  Cap at {cap} trade(s)/match: {stats(kept)}")

    # Win-streak correlation: after a winner, does the next trade in the match win more often?
    next_after_win = []; next_after_loss = []
    for trs in by_match.values():
        trs_sorted = sorted(trs, key=lambda x: x["ns"])
        for i in range(1, len(trs_sorted)):
            p_prev = pnl_for(trs_sorted[i-1], 30)
            p_cur = pnl_for(trs_sorted[i], 30)
            if p_prev is None or p_cur is None: continue
            (next_after_win if p_prev > 0 else next_after_loss).append(p_cur)
    print()
    print(f"  After a winning trade on the match: {stats(next_after_win)}")
    print(f"  After a losing  trade on the match: {stats(next_after_loss)}")


def analysis_6_edge_sizing(trades):
    print("\n" + "="*78)
    print("ANALYSIS #6: Edge-weighted sizing")
    print("="*78)
    # Define "edge proxy" as |d_lead_raw| / ref_norm. Stronger NW into a less-priced market = higher edge.
    # Simpler: just use |d_lead_raw|.
    print("  Sizing-by-|Δ_lead| buckets (flat baseline = $5):")
    buckets = [(1500,2000,1.0),(2000,3000,1.0),(3000,5000,1.0),(5000,99999,1.0)]
    # Show baseline returns by bucket
    print()
    for lo, hi, _ in buckets:
        sub = [pnl_for(t, 30) for t in trades if lo<=abs(t["d_lead_raw"])<hi]
        sub = [p for p in sub if p is not None]
        if sub: print(f"  baseline |Δ| in [{lo:>4}, {hi:>5}): {stats(sub)}")
    # Now apply multiplier: small NW gets 1.5x, big NW gets 0.5x
    print("\n  Re-sized: 1.5× for [1500,2800), 1.0× for [2800,5000), 0.5× for ≥5000:")
    resized = []
    for t in trades:
        p = pnl_for(t, 30)
        if p is None: continue
        dl = abs(t["d_lead_raw"])
        mult = 1.5 if dl<2800 else (1.0 if dl<5000 else 0.5)
        resized.append(p * mult)
    print(f"    {stats(resized)}")
    # vs flat
    flat = [pnl_for(t, 30) for t in trades]
    flat = [p for p in flat if p is not None]
    print(f"\n  Flat sizing (baseline): {stats(flat)}")


def analysis_7_cooldown(trades):
    print("\n" + "="*78)
    print("ANALYSIS #7: Cooldown / time-between-trades on same match")
    print("="*78)
    by_match = defaultdict(list)
    for t in trades:
        by_match[t["mid"]].append(t)
    # Time-between-trades distribution + correlation with EV
    gap_buckets = [(0,15),(15,30),(30,60),(60,180),(180,600),(600,99999)]
    bucket_pnls = {b: [] for b in gap_buckets}
    for trs in by_match.values():
        trs_sorted = sorted(trs, key=lambda x: x["ns"])
        for i in range(1, len(trs_sorted)):
            gap_s = (trs_sorted[i]["ns"] - trs_sorted[i-1]["ns"]) / 1e9
            p = pnl_for(trs_sorted[i], 30)
            if p is None: continue
            for lo, hi in gap_buckets:
                if lo <= gap_s < hi:
                    bucket_pnls[(lo,hi)].append(p)
                    break
    print("  Per-second-gap-since-last-trade-on-match performance of the *next* trade:")
    for (lo, hi), pnls in bucket_pnls.items():
        if pnls:
            print(f"  gap [{lo:>3}, {hi:>5})s: {stats(pnls)}")


def main():
    print("Loading data_v2 and building feature-rich trade ledger...")
    trades, book = build_ledger()
    print(f"Built {len(trades)} trades over {len(set(t['mid'] for t in trades))} matches\n")

    analysis_1_premium(trades)
    analysis_2_exit_timing(trades)
    analysis_3_spread(trades)
    analysis_4_phase(trades)
    analysis_5_multi_trade(trades)
    analysis_6_edge_sizing(trades)
    analysis_7_cooldown(trades)


if __name__ == "__main__":
    main()
