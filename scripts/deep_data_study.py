#!/usr/bin/env python3
"""Deep data study on data_v2.

Sections:
    A. Tournament-regime split (Tournament 1 vs 2 — do findings hold up?)
    B. Loss anatomy (what features predict losing trades?)
    C. Event-type comparison (all primary events, not just FIGHT_SWING)
    D. Book microstructure (ask_size, depth predictive value)
    E. Pre-game pricing (does pre-game price predict in-game alpha?)
    F. Inter-snapshot interval analysis (does cadence quality matter?)
    G. Settle-out timing (when does the game become priced-in?)
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

NOTIONAL = 5.0
COST = 0.005
REF_LO, REF_HI = 0.30, 0.85
NORMAL_GAP_NS = 75 * 1_000_000_000

# Tournament dividing line: bot was paused 2026-05-24/25.
# May 15 → 23 = "Tournament 1" (BLAST, narrow capture)
# May 26 → 28 = "Tournament 2" (multi-tournament, broad capture)
TOURNAMENT_1_END = int(datetime(2026, 5, 24, 0, 0, 0, tzinfo=timezone.utc).timestamp() * 1e9)


# ---------- loaders ----------
def load_mapping():
    with open(REPO_ROOT / "markets.yaml") as f:
        mk = yaml.safe_load(f)
    return {m["dota_match_id"]: {
        "yes": m["yes_token_id"], "no": m["no_token_id"],
        "side": m.get("steam_side_mapping","normal"),
        "name": m.get("name",""),
    } for m in mk["markets"]
        if m.get("dota_match_id") and m["dota_match_id"].isdigit() and m["dota_match_id"]!="123"}


def load_snapshots():
    ds = pds.dataset(REPO_ROOT/"data_v2"/"snapshots", format="parquet", partitioning="hive")
    t = ds.to_table(columns=["match_id","received_at_ns","game_time_sec","league_id",
                              "radiant_lead","radiant_score","dire_score","data_source"])
    by_match = defaultdict(list)
    leagues = {}
    for i in range(t.num_rows):
        mid = t["match_id"][i].as_py()
        leagues[mid] = t["league_id"][i].as_py()
        if t["data_source"][i].as_py() != "top_live":
            continue
        by_match[mid].append({
            "ns": t["received_at_ns"][i].as_py(),
            "gt": t["game_time_sec"][i].as_py(),
            "rl": t["radiant_lead"][i].as_py() or 0,
            "rs": t["radiant_score"][i].as_py() or 0,
            "ds": t["dire_score"][i].as_py() or 0,
        })
    for mid in by_match:
        by_match[mid].sort(key=lambda x: x["ns"])
    return by_match, leagues


def load_book(tokens):
    ds = pds.dataset(REPO_ROOT/"data_v2"/"book_ticks", format="parquet", partitioning="hive")
    t = ds.to_table(columns=["asset_id","received_at_ns","mid","spread","ask_size","bid_size"],
                    filter=pc.is_in(pc.field("asset_id"), pa.array(list(tokens))))
    by_asset = defaultdict(list)
    for i in range(t.num_rows):
        a = t["asset_id"][i].as_py()
        m = t["mid"][i].as_py()
        if m is None: continue
        by_asset[a].append((t["received_at_ns"][i].as_py(), m,
                            t["spread"][i].as_py(), t["ask_size"][i].as_py(),
                            t["bid_size"][i].as_py()))
    for a in by_asset: by_asset[a].sort()
    return {a: tuple(zip(*by_asset[a])) for a in by_asset}


def book_at(book, asset_id, ns):
    arr = book.get(asset_id)
    if not arr: return None
    times, mids, spreads, ask_sizes, bid_sizes = arr
    i = bisect.bisect_right(times, ns) - 1
    if i < 0: return None
    return {"mid": mids[i], "spread": spreads[i], "ask_size": ask_sizes[i], "bid_size": bid_sizes[i]}


def fs_fires(prev, cur):
    if cur["ns"] - prev["ns"] > NORMAL_GAP_NS: return False
    dl = cur["rl"] - prev["rl"]
    dk = (cur["rs"] - prev["rs"]) - (cur["ds"] - prev["ds"])
    if abs(dl) < 1500 or abs(dk) < 1: return False
    if (dl > 0) != (dk > 0): return False
    return True


def build_ledger():
    m2info = load_mapping()
    snaps, leagues = load_snapshots()
    joinable = [mid for mid in snaps if mid in m2info]
    book = load_book({m2info[mid]["yes"] for mid in joinable})
    joined = [mid for mid in joinable if m2info[mid]["yes"] in book]
    trades = []
    for mid in joined:
        info = m2info[mid]
        sign = 1 if info["side"]=="normal" else -1
        yes_tok = info["yes"]
        srows = snaps[mid]
        first_ns = srows[0]["ns"] if srows else 0
        for i in range(1, len(srows)):
            prev, cur = srows[i-1], srows[i]
            if cur["gt"] is None or cur["gt"] < 300: continue
            if not fs_fires(prev, cur): continue
            dl_yes = (cur["rl"] - prev["rl"]) * sign
            d = 1 if dl_yes > 0 else (-1 if dl_yes < 0 else 0)
            if d == 0: continue
            b0 = book_at(book, yes_tok, cur["ns"])
            if b0 is None: continue
            if not (REF_LO <= b0["mid"] <= REF_HI): continue
            mks = {}
            for h in [3, 5, 10, 20, 30, 60, 120]:
                b_h = book_at(book, yes_tok, cur["ns"] + h*1_000_000_000)
                mks[h] = (b_h["mid"] - b0["mid"]) if b_h else None
            if mks[30] is None: continue
            # Snapshot-cadence quality at this point
            gap_to_prev = (cur["ns"] - prev["ns"]) / 1e9
            trades.append({
                "mid": mid, "ns": cur["ns"], "first_ns": first_ns,
                "league": leagues.get(mid, "0"),
                "gt": cur["gt"], "d_lead_raw": cur["rl"] - prev["rl"], "d_lead_yes": dl_yes,
                "cur_lead_yes": cur["rl"] * sign, "direction": d,
                "ref": b0["mid"], "spread": b0["spread"],
                "ask_size": b0["ask_size"], "bid_size": b0["bid_size"],
                "markouts": mks, "yes_tok": yes_tok, "snap_gap": gap_to_prev,
            })
    return trades, book, snaps, m2info, leagues


def pnl(t, h=30):
    dm = t["markouts"].get(h)
    if dm is None: return None
    return ((dm * t["direction"] - COST) / t["ref"]) * NOTIONAL


def stats(pnls, label=""):
    if not pnls: return f"{label}: 0"
    p = sorted(pnls); n=len(p); w=sum(1 for x in p if x>0)
    return (f"n={n:>4} ${sum(p):+7.2f} ${sum(p)/n:+.3f}/t win={100*w/n:>3.0f}% "
            f"med=${p[n//2]:+.3f}")


def classify(t):
    fav = t["direction"]; lead_fav = t["cur_lead_yes"] * fav
    if abs(lead_fav) < 1000: return "close_game"
    if lead_fav > 0: return "lead_extension"
    return "comeback_against"


# ============================================================================
# SECTION A: Tournament regime split
# ============================================================================
def section_a_tournaments(trades):
    print("\n" + "="*78)
    print("A. TOURNAMENT REGIME SPLIT (Tournament 1: May 15-23, Tournament 2: May 26-28)")
    print("="*78)
    t1 = [t for t in trades if t["ns"] < TOURNAMENT_1_END]
    t2 = [t for t in trades if t["ns"] >= TOURNAMENT_1_END]
    print(f"\nTournament 1 trades: {len(t1)} on {len(set(t['mid'] for t in t1))} matches")
    print(f"Tournament 2 trades: {len(t2)} on {len(set(t['mid'] for t in t2))} matches")

    p1 = [pnl(t,30) for t in t1]; p1 = [p for p in p1 if p is not None]
    p2 = [pnl(t,30) for t in t2]; p2 = [p for p in p2 if p is not None]
    print(f"\nFull strategy (FS + ref-band, 30s):")
    print(f"  Tournament 1: {stats(p1)}")
    print(f"  Tournament 2: {stats(p2)}")

    # Do the key findings hold up in each regime?
    print(f"\nLead-extension subgroup (workhorse):")
    for name, sub in [("T1", t1), ("T2", t2)]:
        cls = [pnl(t,30) for t in sub if classify(t)=="lead_extension"]
        cls = [p for p in cls if p is not None]
        print(f"  {name} lead_extension: {stats(cls)}")

    print(f"\nSpread-band [0.02, 0.05) (the toxic zone):")
    for name, sub in [("T1", t1), ("T2", t2)]:
        toxic = [pnl(t,30) for t in sub if t["spread"] is not None and 0.02<=t["spread"]<0.05]
        toxic = [p for p in toxic if p is not None]
        print(f"  {name} toxic spread band: {stats(toxic)}")

    print(f"\n45-50min phase (the weak zone):")
    for name, sub in [("T1", t1), ("T2", t2)]:
        weak = [pnl(t,30) for t in sub if 2700<=t["gt"]<3000]
        weak = [p for p in weak if p is not None]
        print(f"  {name} 45-50m: {stats(weak)}")


# ============================================================================
# SECTION B: Loss anatomy
# ============================================================================
def section_b_losses(trades):
    print("\n" + "="*78)
    print("B. LOSS ANATOMY (what predicts losing trades?)")
    print("="*78)
    losses = [t for t in trades if (pnl(t,30) or 0) < 0]
    wins = [t for t in trades if (pnl(t,30) or 0) > 0]
    print(f"Wins: {len(wins)}, Losses: {len(losses)}")

    def avg(vals): return sum(vals)/len(vals) if vals else 0

    print(f"\n{'feature':<25} {'win mean':>12} {'loss mean':>12} {'diff':>10}")
    for label, fn in [
        ("ref price",            lambda t: t["ref"]),
        ("spread",               lambda t: t["spread"] or 0),
        ("ask_size",             lambda t: t["ask_size"] or 0),
        ("game_time_sec",        lambda t: t["gt"]),
        ("|Δ_lead|",             lambda t: abs(t["d_lead_raw"])),
        ("|cur_lead_yes|",       lambda t: abs(t["cur_lead_yes"])),
        ("snap_gap (s)",         lambda t: t["snap_gap"]),
        ("lead-extension",       lambda t: 1 if classify(t)=="lead_extension" else 0),
        ("comeback_against",     lambda t: 1 if classify(t)=="comeback_against" else 0),
        ("close_game",           lambda t: 1 if classify(t)=="close_game" else 0),
    ]:
        win_avg = avg([fn(t) for t in wins])
        loss_avg = avg([fn(t) for t in losses])
        print(f"{label:<25} {win_avg:>12.3f} {loss_avg:>12.3f} {win_avg-loss_avg:>+10.3f}")

    # Biggest losses — what are they?
    losses_sorted = sorted(losses, key=lambda t: pnl(t,30))
    print(f"\n=== 8 worst losing trades ===")
    print(f"{'pnl':>7} {'gt':>5} {'Δlead':>6} {'cur_l':>7} {'ref':>6} {'spr':>5} {'cls':<18}")
    for t in losses_sorted[:8]:
        p = pnl(t,30)
        print(f"  {p:+.3f} {t['gt']:>5} {t['d_lead_raw']:>+6} {t['cur_lead_yes']:>+7} "
              f"{t['ref']:.3f} {t['spread'] or 0:.3f} {classify(t):<18}")


# ============================================================================
# SECTION C: Event-type comparison via Parquet signals + markouts
# ============================================================================
def section_c_events():
    print("\n" + "="*78)
    print("C. EVENT-TYPE COMPARISON (all primary events, not just FIGHT_SWING)")
    print("="*78)
    sig_ds = pds.dataset(REPO_ROOT/"data_v2"/"signals", format="parquet", partitioning="hive")
    mk_ds  = pds.dataset(REPO_ROOT/"data_v2"/"markouts", format="parquet", partitioning="hive")
    sig = sig_ds.to_table(columns=["signal_id","event_type","decision","fair_price","executable_edge","ask","spread"])
    mk30 = mk_ds.to_table(columns=["signal_id","horizon_sec","markout_price_delta","reference_price","decision_at_signal"],
                          filter=pc.field("horizon_sec")==30)
    import pandas as pd
    s = sig.to_pandas(); m = mk30.to_pandas()
    j = s.merge(m, on="signal_id")
    # For "would-have-traded" view, treat every signal as if executed at reference_price
    # and use markout as raw price-impact (no spread cost — signal-level analysis).
    j = j.dropna(subset=["reference_price","markout_price_delta"])
    j = j[(j["reference_price"] > 0.05) & (j["reference_price"] < 0.95)]
    j["raw_return"] = j["markout_price_delta"] / j["reference_price"]
    print(f"Joined signals + 30s markouts: {len(j):,}")
    print(f"\n{'event_type':<30} {'n':>5} {'mean_return':>11} {'win%':>6} {'med':>8}")
    g = j.groupby("event_type").agg(
        n=("signal_id","count"),
        mean_ret=("raw_return","mean"),
        med_ret=("raw_return","median"),
        win=("raw_return", lambda x: (x>0).mean()*100),
    ).sort_values("n", ascending=False)
    for et, r in g.iterrows():
        if r["n"] < 10: continue
        print(f"  {et:<30} {int(r['n']):>5} {r['mean_ret']*100:>+10.2f}% {r['win']:>5.0f}% {r['med_ret']*100:>+7.2f}%")


# ============================================================================
# SECTION D: Book microstructure
# ============================================================================
def section_d_microstructure(trades):
    print("\n" + "="*78)
    print("D. BOOK MICROSTRUCTURE (ask_size, bid_size predictive value)")
    print("="*78)
    print(f"{'ask_size bucket':<22} {'$/t':>10} {'win%':>6} {'n':>4}")
    buckets = [(0,10),(10,50),(50,100),(100,500),(500,2000),(2000,99999)]
    for lo, hi in buckets:
        sub = [pnl(t,30) for t in trades if t["ask_size"] is not None and lo<=t["ask_size"]<hi]
        sub = [p for p in sub if p is not None]
        if len(sub) < 5: continue
        n=len(sub); w=sum(1 for p in sub if p>0)
        print(f"  [{lo:>4}, {hi:>5})       {sum(sub)/n:>+8.3f}  {100*w/n:>4.0f}%  {n:>4}")

    print(f"\nbid_size:")
    for lo, hi in buckets:
        sub = [pnl(t,30) for t in trades if t["bid_size"] is not None and lo<=t["bid_size"]<hi]
        sub = [p for p in sub if p is not None]
        if len(sub) < 5: continue
        n=len(sub); w=sum(1 for p in sub if p>0)
        print(f"  [{lo:>4}, {hi:>5})       {sum(sub)/n:>+8.3f}  {100*w/n:>4.0f}%  {n:>4}")

    # Ask/bid ratio (imbalance)
    print(f"\nbook imbalance (ask_size / (ask_size + bid_size)):")
    for lo, hi in [(0,0.2),(0.2,0.4),(0.4,0.5),(0.5,0.6),(0.6,0.8),(0.8,1.0)]:
        sub = [pnl(t,30) for t in trades
               if t["ask_size"] is not None and t["bid_size"] is not None
               and (t["ask_size"]+t["bid_size"]) > 0
               and lo <= t["ask_size"]/(t["ask_size"]+t["bid_size"]) < hi]
        sub = [p for p in sub if p is not None]
        if len(sub) < 5: continue
        n=len(sub); w=sum(1 for p in sub if p>0)
        print(f"  ask/(ask+bid) [{lo:.1f}, {hi:.1f}):  {sum(sub)/n:>+8.3f}  {100*w/n:>4.0f}%  {n:>4}")


# ============================================================================
# SECTION E: Pre-game pricing
# ============================================================================
def section_e_pregame(trades, snaps, m2info, book):
    print("\n" + "="*78)
    print("E. PRE-GAME PRICING (does pre-game price predict in-game alpha?)")
    print("="*78)
    # For each trade, find the earliest book mid for the YES token (anchored to t-30min before first snapshot)
    earliest_mid = {}
    for mid in {t["mid"] for t in trades}:
        yes_tok = m2info[mid]["yes"]
        arr = book.get(yes_tok)
        if not arr: continue
        times, mids, *_ = arr
        if mids: earliest_mid[mid] = mids[0]
    print(f"Pre-game prices found for {len(earliest_mid)} matches")

    # Bucket: did pre-game price favor the side that ended up gaining edge?
    by_bucket = defaultdict(list)
    for t in trades:
        pg = earliest_mid.get(t["mid"])
        if pg is None: continue
        # Distance from 0.50 indicates pre-game favoritism
        # For YES-favored signals (direction=+1), bigger pg = more favoritism for YES
        signed_pg = (pg - 0.5) * t["direction"]
        p = pnl(t, 30)
        if p is None: continue
        if signed_pg < -0.2: by_bucket["strong_underdog"].append(p)
        elif signed_pg < -0.05: by_bucket["mild_underdog"].append(p)
        elif signed_pg < 0.05: by_bucket["close_50_50"].append(p)
        elif signed_pg < 0.2: by_bucket["mild_favorite"].append(p)
        else: by_bucket["strong_favorite"].append(p)

    print(f"\nPnL by pre-game positioning (signed by signal direction):")
    for k in ["strong_underdog","mild_underdog","close_50_50","mild_favorite","strong_favorite"]:
        s = by_bucket[k]
        if not s: continue
        n=len(s); w=sum(1 for p in s if p>0)
        print(f"  {k:<22} n={n:>4} ${sum(s):+7.2f}  ${sum(s)/n:+.3f}/t  win={100*w/n:>3.0f}%")


# ============================================================================
# SECTION F: Inter-snapshot interval analysis
# ============================================================================
def section_f_intervals(trades):
    print("\n" + "="*78)
    print("F. INTER-SNAPSHOT INTERVAL ANALYSIS (snap_gap predicts alpha?)")
    print("="*78)
    print(f"{'snap_gap (s)':<22} {'$/t':>10} {'win%':>6} {'n':>4}")
    buckets = [(0,5),(5,10),(10,15),(15,20),(20,30),(30,45),(45,75)]
    for lo, hi in buckets:
        sub = [pnl(t,30) for t in trades if lo<=t["snap_gap"]<hi]
        sub = [p for p in sub if p is not None]
        if not sub: continue
        n=len(sub); w=sum(1 for p in sub if p>0)
        print(f"  [{lo:>2}, {hi:>2})           {sum(sub)/n:>+8.3f}  {100*w/n:>4.0f}%  {n:>4}")


# ============================================================================
# SECTION G: Settle-out timing
# ============================================================================
def section_g_settleout(trades):
    print("\n" + "="*78)
    print("G. SETTLE-OUT TIMING (when does the game become priced-in / alpha disappear?)")
    print("="*78)
    print("For lead_extension trades, alpha by current price (already-priced-in):")
    for ref_lo, ref_hi in [(0.30,0.50),(0.50,0.65),(0.65,0.75),(0.75,0.80),(0.80,0.85)]:
        for h in [30, 60, 120]:
            sub = [pnl(t,h) for t in trades
                   if classify(t)=="lead_extension" and ref_lo<=t["ref"]<ref_hi]
            sub = [p for p in sub if p is not None]
            if len(sub) >= 5:
                n=len(sub); w=sum(1 for p in sub if p>0)
                print(f"  ref [{ref_lo:.2f}, {ref_hi:.2f})  hold {h:>3}s:  ${sum(sub)/n:>+7.3f}/t  win={100*w/n:>3.0f}%  n={n}")
        print()


# ============================================================================
def main():
    print("Loading data_v2 and building trade ledger...")
    trades, book, snaps, m2info, leagues = build_ledger()
    print(f"Built {len(trades)} trades over {len(set(t['mid'] for t in trades))} matches")
    section_a_tournaments(trades)
    section_b_losses(trades)
    section_c_events()
    section_d_microstructure(trades)
    section_e_pregame(trades, snaps, m2info, book)
    section_f_intervals(trades)
    section_g_settleout(trades)


if __name__ == "__main__":
    main()
