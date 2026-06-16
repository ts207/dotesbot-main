#!/usr/bin/env python3
"""Reproduce the POLL_FIGHT_SWING + ref-band-gate backtest reading from
`data_v2/` Parquet instead of the legacy `logs/` CSVs.

Earlier result (from CSVs, 2026-05-28):
    n=234, total=+$65.83, $/trade=+$0.281, win=59%

A clean reproduction with identical (or near-identical) numbers verifies
that the v2 pipeline is faithful end-to-end.

Usage:
    python3 scripts/backtest_from_v2.py
"""
from __future__ import annotations

import bisect
import sys
import time
from collections import defaultdict
from datetime import timedelta
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


# ---------- load mapping ----------

def load_mapping():
    with open(REPO_ROOT / "markets.yaml") as f:
        mk = yaml.safe_load(f)
    m2info = {}
    for m in mk["markets"]:
        mid = m.get("dota_match_id")
        if mid and mid.isdigit() and mid != "123":
            m2info[mid] = {
                "yes": m["yes_token_id"],
                "no":  m["no_token_id"],
                "side": m.get("steam_side_mapping", "normal"),
            }
    return m2info


# ---------- load snapshots ----------

def load_snapshots():
    ds = pds.dataset(REPO_ROOT / "data_v2" / "snapshots", format="parquet", partitioning="hive")
    cols = ["match_id", "received_at_ns", "game_time_sec",
            "radiant_lead", "radiant_score", "dire_score", "data_source"]
    t = ds.to_table(columns=cols, filter=pc.field("data_source") == "top_live")
    snaps = defaultdict(list)
    for i in range(t.num_rows):
        match_id = t["match_id"][i].as_py()
        snaps[match_id].append({
            "ns": t["received_at_ns"][i].as_py(),
            "gt": t["game_time_sec"][i].as_py(),
            "rl": t["radiant_lead"][i].as_py() or 0,
            "rs": t["radiant_score"][i].as_py() or 0,
            "ds": t["dire_score"][i].as_py() or 0,
        })
    for mid in snaps:
        snaps[mid].sort(key=lambda x: x["ns"])
    return snaps


# ---------- load book ticks (only mid, indexed for fast at-time lookup) ----------

def load_book_ticks(tokens):
    """Return {asset_id: ([sorted ns], [mid]) }, restricted to `tokens`."""
    ds = pds.dataset(REPO_ROOT / "data_v2" / "book_ticks", format="parquet", partitioning="hive")
    # Use the partition column to prune; we already know the dates we want.
    t = ds.to_table(columns=["asset_id", "received_at_ns", "mid"],
                    filter=pc.is_in(pc.field("asset_id"), pa.array(list(tokens))))
    by_asset = defaultdict(list)
    for i in range(t.num_rows):
        a = t["asset_id"][i].as_py()
        m = t["mid"][i].as_py()
        if m is None:
            continue
        by_asset[a].append((t["received_at_ns"][i].as_py(), m))
    for a in by_asset:
        by_asset[a].sort()
    return {a: (
        [x[0] for x in by_asset[a]],
        [x[1] for x in by_asset[a]],
    ) for a in by_asset}


def book_mid_at(book, asset_id, ns):
    arr = book.get(asset_id)
    if not arr:
        return None
    times, mids = arr
    i = bisect.bisect_right(times, ns) - 1
    if i < 0:
        return None
    return mids[i]

def get_asset_winner(book, asset_id):
    """Determine if asset won or lost based on its final traded price."""
    arr = book.get(asset_id)
    if not arr:
        return None
    times, mids = arr
    if not mids:
        return None
    final_mid = mids[-1]
    if final_mid > 0.90:
        return 1
    elif final_mid < 0.10:
        return 0
    else:
        return None  # Unresolved or suspended market

# ---------- FIGHT_SWING detector + ref-band gate ----------

def fs_fires(prev, cur):
    if cur["ns"] - prev["ns"] > NORMAL_GAP_NS:
        return False
    dl = cur["rl"] - prev["rl"]
    dk = (cur["rs"] - prev["rs"]) - (cur["ds"] - prev["ds"])
    if abs(dl) < 1500 or abs(dk) < 1:
        return False
    if (dl > 0) != (dk > 0):
        return False
    return True


# ---------- backtest ----------

def main():
    t0 = time.time()
    m2info = load_mapping()
    print(f"Loaded mapping: {len(m2info)} mapped matches  ({time.time()-t0:.2f}s)")

    t0 = time.time()
    snaps = load_snapshots()
    print(f"Loaded snapshots: {len(snaps)} matches, {sum(len(v) for v in snaps.values()):,} top_live snapshots  ({time.time()-t0:.2f}s)")

    # Identify joinable matches up front, then load just those tokens' book ticks.
    joinable = [mid for mid in snaps if mid in m2info]
    tokens_yes = {m2info[mid]["yes"] for mid in joinable}
    print(f"Joinable matches (snapshots + mapping): {len(joinable)}")

    t0 = time.time()
    book = load_book_ticks(tokens_yes)
    print(f"Loaded book ticks: {len(book)} assets, "
          f"{sum(len(v[0]) for v in book.values()):,} ticks  ({time.time()-t0:.2f}s)")

    joined_mids = [mid for mid in joinable if m2info[mid]["yes"] in book]
    print(f"Final joined set (snapshots + mapping + book): {len(joined_mids)}")

    # Run two variants — match the two earlier baselines.
    t0 = time.time()
    trades_ref_only: list[dict] = []   # ref-band gate only
    trades_full: list[dict] = []       # ref-band + dead-zone gate (20-30m excluded)
    for mid in joined_mids:
        info = m2info[mid]
        sign = 1 if info["side"] == "normal" else -1
        yes_tok = info["yes"]
        
        yes_won = get_asset_winner(book, yes_tok)
        if yes_won is None:
            continue
            
        srows = snaps[mid]
        for i in range(1, len(srows)):
            prev, cur = srows[i - 1], srows[i]
            if cur["gt"] is None or cur["gt"] < 300:
                continue
            if not fs_fires(prev, cur):
                continue
            dl_yes = (cur["rl"] - prev["rl"]) * sign
            direction = 1 if dl_yes > 0 else (-1 if dl_yes < 0 else 0)
            if direction == 0:
                continue
            mid0 = book_mid_at(book, yes_tok, cur["ns"])
            if mid0 is None:
                continue
            if not (REF_LOW <= mid0 <= REF_HIGH):
                continue
            
            ask = mid0 + COST_HALF_SPREAD
            # Settlement PnL
            # If direction == 1, we bought YES. If direction == -1, we bought NO (short YES).
            if direction == 1:
                payout = 1.0 if yes_won == 1 else 0.0
            else:
                payout = 1.0 if yes_won == 0 else 0.0
                
            pnl = (payout - ask) * NOTIONAL
            
            row = {"mid": mid, "gt": cur["gt"], "ref": mid0,
                   "d_lead": cur["rl"] - prev["rl"], "pnl": pnl}
            trades_ref_only.append(row)
            if not (1200 <= cur["gt"] < 1800):    # NOT in dead zone
                trades_full.append(row)
    print(f"Backtest core: {time.time()-t0:.2f}s")

    def report(label, trades, ref_n, ref_total, ref_pt, ref_win):
        if not trades:
            print(f"\n=== {label}: no trades ===")
            return
        pnls = sorted(t["pnl"] for t in trades)
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        total = sum(pnls)
        print(f"\n=== {label} ===")
        print(f"  trades:   {n:>4}   (reference: {ref_n})   drift: {n - ref_n:+d}")
        print(f"  total:    ${total:+8.2f}   (reference: +${ref_total:.2f})   drift: ${total - ref_total:+.2f}")
        print(f"  $/trade:  ${total/n:+8.4f}   (reference: +${ref_pt:.4f})   drift: ${total/n - ref_pt:+.4f}")
        print(f"  win%:     {100*wins/n:>4.1f}%   (reference: {ref_win:.0f}%)")
        print(f"  median:   ${pnls[n//2]:+.4f}   p25: ${pnls[n//4]:+.4f}   p75: ${pnls[3*n//4]:+.4f}")

    # ref-only baseline: earlier numbers were n=307, +$78.18, $0.255/t, 59%
    report("Variant A: ref-band gate only", trades_ref_only,
           ref_n=307, ref_total=78.18, ref_pt=0.2547, ref_win=59)
    # ref+dead-zone baseline: earlier numbers were n=234, +$65.83, $0.281/t, 59%
    report("Variant B: ref-band + dead-zone (20-30m excluded)", trades_full,
           ref_n=234, ref_total=65.83, ref_pt=0.2814, ref_win=59)

    print("\nFidelity assessment:")
    drift_a = abs(len(trades_ref_only) - 307) / 307
    drift_b = abs(len(trades_full) - 234) / 234
    if drift_a < 0.02 and drift_b < 0.02:
        print(f"  ✅ Trade-count drift under 2% on both variants ({drift_a*100:.1f}% / {drift_b*100:.1f}%)")
    else:
        print(f"  ⚠️  Drift exceeds 2% on at least one variant ({drift_a*100:.1f}% / {drift_b*100:.1f}%)")


if __name__ == "__main__":
    main()
