#!/usr/bin/env python3
"""End-to-end replay: drive `continuous_scorer.score_snapshot` from data_v2
and confirm the trade count and PnL land near the 185-trade snapshot_book
study reference (n=185, +$77.46 at $5 base, $0.42/trade, 64% win).
"""
from __future__ import annotations

import bisect
import sys
import time
from collections import defaultdict
from pathlib import Path

import yaml
import pyarrow as pa
import pyarrow.dataset as pds
import pyarrow.compute as pc

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from continuous_scorer import score_snapshot, ContinuousSignal, ScoreReject, EXIT_HORIZON_SEC


# Local copies of loaders (cheaper than importing the study module).
def _load_mapping():
    with open(REPO_ROOT / "markets.yaml") as f:
        mk = yaml.safe_load(f)
    return {m["dota_match_id"]: {
        "yes": m["yes_token_id"], "no": m["no_token_id"],
        "steam_side_mapping": m.get("steam_side_mapping", "normal"),
    } for m in mk["markets"]
        if m.get("dota_match_id") and m["dota_match_id"].isdigit() and m["dota_match_id"]!="123"}


def _load_snapshots():
    ds = pds.dataset(REPO_ROOT/"data_v2"/"snapshots", format="parquet", partitioning="hive")
    t = ds.to_table(columns=["match_id","received_at_ns","game_time_sec",
                              "radiant_lead","radiant_score","dire_score","data_source"],
                    filter=pc.field("data_source")=="top_live")
    snaps = defaultdict(list)
    for i in range(t.num_rows):
        snaps[t["match_id"][i].as_py()].append({
            "match_id": t["match_id"][i].as_py(),
            "received_at_ns": t["received_at_ns"][i].as_py(),
            "game_time_sec": t["game_time_sec"][i].as_py(),
            "radiant_lead": t["radiant_lead"][i].as_py() or 0,
            "radiant_score": t["radiant_score"][i].as_py() or 0,
            "dire_score": t["dire_score"][i].as_py() or 0,
        })
    for mid in snaps: snaps[mid].sort(key=lambda x: x["received_at_ns"])
    return snaps


def _load_book(tokens):
    ds = pds.dataset(REPO_ROOT/"data_v2"/"book_ticks", format="parquet", partitioning="hive")
    t = ds.to_table(columns=["asset_id","received_at_ns","mid","best_ask","best_bid","ask_size","bid_size"],
                    filter=pc.is_in(pc.field("asset_id"), pa.array(list(tokens))))
    by = defaultdict(list)
    for i in range(t.num_rows):
        a = t["asset_id"][i].as_py()
        by[a].append({
            "received_at_ns": t["received_at_ns"][i].as_py(),
            "mid": t["mid"][i].as_py(),
            "best_ask": t["best_ask"][i].as_py(),
            "best_bid": t["best_bid"][i].as_py(),
            "ask_size": t["ask_size"][i].as_py(),
            "bid_size": t["bid_size"][i].as_py(),
        })
    for a in by: by[a].sort(key=lambda x: x["received_at_ns"])
    return by


def _book_at(book, asset_id, ns):
    arr = book.get(asset_id)
    if not arr: return None
    times = [x["received_at_ns"] for x in arr]
    i = bisect.bisect_right(times, ns) - 1
    if i < 0: return None
    return arr[i]


def main():
    t0 = time.time()
    mapping = _load_mapping()
    snaps = _load_snapshots()
    print(f"Loaded {len(snaps)} matches with top_live snapshots ({time.time()-t0:.2f}s)")

    joinable = [mid for mid in snaps if mid in mapping]
    yes_tokens = {mapping[mid]["yes"] for mid in joinable}
    no_tokens  = {mapping[mid]["no"]  for mid in joinable}
    all_tokens = yes_tokens | no_tokens

    t0 = time.time()
    book = _load_book(all_tokens)
    print(f"Loaded book for {len(book)} assets ({time.time()-t0:.2f}s)")

    joined = [mid for mid in joinable
              if mapping[mid]["yes"] in book and mapping[mid]["no"] in book]
    print(f"Joined matches (snapshots + mapping + YES book + NO book): {len(joined)}")

    # Drive the scorer
    n_signals = 0
    n_rejects = 0
    reject_reasons = defaultdict(int)
    signals: list = []
    pregame_mid_cache: dict = {}

    for mid in joined:
        info = mapping[mid]
        yes_tok, no_tok = info["yes"], info["no"]
        # pregame anchor: first known YES mid for this match
        yes_arr = book[yes_tok]
        pregame_yes_mid = next((b["mid"] for b in yes_arr if b["mid"] is not None), 0.5)
        pregame_mid_cache[mid] = pregame_yes_mid

        srows = snaps[mid]
        for i in range(1, len(srows)):
            prev_snap, cur_snap = srows[i-1], srows[i]
            yes_b = _book_at(book, yes_tok, cur_snap["received_at_ns"])
            no_b  = _book_at(book, no_tok,  cur_snap["received_at_ns"])
            if yes_b is None or no_b is None:
                n_rejects += 1
                reject_reasons["book_lookup_failed"] += 1
                continue

            res = score_snapshot(
                prev_snap=prev_snap,
                cur_snap=cur_snap,
                yes_book=yes_b,
                no_book=no_b,
                pregame_yes_mid=pregame_yes_mid,
                mapping=info,
            )
            if isinstance(res, ContinuousSignal):
                n_signals += 1
                signals.append((res, prev_snap, cur_snap, yes_tok, no_tok))
            else:
                n_rejects += 1
                reject_reasons[res.reason] += 1

    print(f"\nScored: {n_signals + n_rejects:,} snapshot pairs")
    print(f"  signals fired: {n_signals:,}")
    print(f"  rejects:       {n_rejects:,}")
    print(f"\nTop reject reasons:")
    for r, n in sorted(reject_reasons.items(), key=lambda x:-x[1])[:10]:
        print(f"  {r:<35} {n:>6,}")

    # Compute PnL: hold for EXIT_HORIZON_SEC, exit at the opposite side's bid.
    # For YES position: exit at YES_bid at t+hold. For NO position: exit at NO_bid.
    # Cost model = 0.5c half-spread (entry pays the ask, exit at the bid — that's
    # roughly 1c full-cross; baking in 0.5c half-cost is the same convention
    # used in the study).
    NOTIONAL_PER_DOLLAR = lambda sz: sz / 5.0  # scorer returns sized_usd; normalize to multiples of $5
    COST_PER_TRADE = 0.005

    trades = []
    for sig, prev_snap, cur_snap, yes_tok, no_tok in signals:
        # exit at sig.received_at_ns + EXIT_HORIZON_SEC
        exit_ns = sig.received_at_ns + sig.exit_horizon_sec * 1_000_000_000
        target_token = yes_tok if sig.side == "YES" else no_tok
        b_entry = _book_at(book, target_token, sig.received_at_ns)
        b_exit  = _book_at(book, target_token, exit_ns)
        if not b_entry or not b_exit: continue
        if b_entry["mid"] is None or b_exit["mid"] is None: continue
        d_mid = b_exit["mid"] - b_entry["mid"]
        # The trade is in the direction of d_mid > 0 (we buy this side to go up).
        ret_per_dollar = (d_mid - COST_PER_TRADE) / b_entry["mid"]
        pnl = ret_per_dollar * sig.sized_usd
        trades.append({"signal_id": sig.signal_id, "match_id": sig.match_id,
                       "direction": sig.direction, "side": sig.side,
                       "sized_usd": sig.sized_usd, "pnl": pnl,
                       "conviction": sig.conviction_mult,
                       "magnitude": sig.magnitude_mult})

    if not trades:
        print("\nNo trades — something is wrong.")
        return

    pnls = sorted(t["pnl"] for t in trades)
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    total = sum(pnls)
    avg_sz = sum(t["sized_usd"] for t in trades) / n
    print("\n" + "="*72)
    print("REPLAY RESULT (continuous scorer driven from data_v2)")
    print("="*72)
    print(f"  trades:        {n:,}")
    print(f"  total PnL:     ${total:+.2f}")
    print(f"  $/trade:       ${total/n:+.4f}")
    print(f"  win rate:      {100*wins/n:.1f}%")
    print(f"  median trade:  ${pnls[n//2]:+.4f}")
    print(f"  p25 / p75:     ${pnls[n//4]:+.4f} / ${pnls[3*n//4]:+.4f}")
    print(f"  avg sized_usd: ${avg_sz:.2f}")
    print()
    print("=== Reference (snapshot_book_study variant E, 60s hold) ===")
    print(f"  trades:   185")
    print(f"  total:    +$77.46")
    print(f"  $/trade:  +$0.4188")
    print(f"  win%:     64%")
    print(f"  avg_size: 1.41x base = $7.05")
    print()
    print("=== Drift vs reference ===")
    print(f"  trades:   {n - 185:+d}")
    print(f"  total:    ${total - 77.46:+.2f}")
    print(f"  $/trade:  ${(total/n) - 0.4188:+.4f}")
    print(f"  win drift: {100*wins/n - 64:+.1f}pp")


if __name__ == "__main__":
    main()
