#!/usr/bin/env python3
"""Test smarter ride-leg exits.

Variants we'll try:
  1. baseline: ride to 0.90, no stop (current production)
  2. immediate: sell ride leg AT its current bid as soon as the other scratches
  3. delayed_5: sell ride leg 5 minutes after the other scratches
  4. delayed_15
  5. ride_below_entry: sell ride leg when its bid drops below entry
  6. ride_below_entry_minus_5c: sell ride leg when bid drops 5c below entry
  7. trail_2c: trailing stop 2c below peak
  8. trail_5c
  9. trail_10c
"""
from __future__ import annotations

import bisect
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml
import pyarrow as pa
import pyarrow.dataset as pds
import pyarrow.compute as pc

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Imports from the investigation script
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from investigate_scalp import (
    SCALP_STAKE_USD, SCALP_SCRATCH_CENTS, SCALP_MAX_HOLD_MIN,
    load_mapping, load_snapshots_with_league, load_book,
    game_time_at, qualifies,
)


def run_scalp_with_ride_exit(
    mapping, snaps, book,
    *,
    exit_strategy: str,
    delay_min: float = 5.0,
    drop_below_entry_cents: float = 0.0,
    trail_cents: float = 0.05,
    ride_tp_threshold: float = 0.90,
    max_game_time_sec: int = 1800,
):
    """Run scalp with one of the alternate ride-leg exit strategies."""
    trades = []

    for mid, info in mapping.items():
        yes_arr = book.get(info["yes"]); no_arr = book.get(info["no"])
        if not yes_arr or not no_arr: continue
        snap_list = snaps.get(mid, [])
        no_times = [n["received_at_ns"] for n in no_arr]

        # find entry
        entry_idx = None
        for i, y in enumerate(yes_arr):
            ns = y["received_at_ns"]
            j = bisect.bisect_right(no_times, ns) - 1
            if j < 0: continue
            n = no_arr[j]
            if abs(ns - n["received_at_ns"]) > 2_000_000_000: continue
            gt = game_time_at(snap_list, ns)
            if qualifies(yes_book=y, no_book=n, game_time_sec=gt, max_game_time_sec=max_game_time_sec):
                entry_idx = (i, j); entry_ns = ns
                entry_y, entry_n = y["best_ask"], n["best_ask"]
                break
        if entry_idx is None: continue

        yes_shares = SCALP_STAKE_USD / entry_y
        no_shares = SCALP_STAKE_USD / entry_n
        yes_status = no_status = "open"
        yes_exit_px = no_exit_px = 0.0
        yes_exit_reason = no_exit_reason = ""
        ride_side = None
        ride_started_ns = None
        ride_peak_bid = 0.0

        i_y, i_n = entry_idx
        events = []
        for y in yes_arr[i_y+1:]: events.append(("Y", y["received_at_ns"], y))
        for n in no_arr[i_n+1:]: events.append(("N", n["received_at_ns"], n))
        events.sort(key=lambda x: x[1])
        cur_y, cur_n = yes_arr[i_y], no_arr[i_n]

        for side, ns, tick in events:
            if side == "Y": cur_y = tick
            else: cur_n = tick
            yb = cur_y.get("best_bid"); nb = cur_n.get("best_bid")

            # Scratch check on still-open legs
            if yes_status == "open" and yb is not None and yb >= entry_y + SCALP_SCRATCH_CENTS:
                yes_status, yes_exit_px, yes_exit_reason = "scratched", yb, "scratched"
            if no_status == "open" and nb is not None and nb >= entry_n + SCALP_SCRATCH_CENTS:
                no_status, no_exit_px, no_exit_reason = "scratched", nb, "scratched"

            # Identify ride
            if ride_side is None:
                y_done = yes_status != "open"
                n_done = no_status != "open"
                if y_done and not n_done:
                    ride_side = "NO"; ride_started_ns = ns
                elif n_done and not y_done:
                    ride_side = "YES"; ride_started_ns = ns
                elif y_done and n_done:
                    break

            # Ride-leg exit logic
            if ride_side is not None:
                ride_leg_status = yes_status if ride_side == "YES" else no_status
                if ride_leg_status == "open":
                    ride_bid = yb if ride_side == "YES" else nb
                    ride_entry = entry_y if ride_side == "YES" else entry_n
                    if ride_bid is not None and ride_bid > ride_peak_bid:
                        ride_peak_bid = ride_bid

                    exit_now = False
                    exit_reason = ""

                    # --- exit_strategy-specific logic ---
                    if exit_strategy == "baseline":
                        if ride_bid is not None and ride_bid >= ride_tp_threshold:
                            exit_now = True; exit_reason = f"tp_{ride_bid:.3f}"
                    elif exit_strategy == "immediate":
                        # Sell at the bid present at the moment the OTHER leg scratched
                        # (i.e., now, on the first tick after ride is identified)
                        if ride_bid is not None:
                            exit_now = True; exit_reason = f"immediate_{ride_bid:.3f}"
                    elif exit_strategy == "delayed":
                        # Sell after N minutes from ride start
                        if ride_started_ns is not None and (ns - ride_started_ns) / 60e9 >= delay_min:
                            if ride_bid is not None:
                                exit_now = True; exit_reason = f"delayed_{delay_min:.0f}min_{ride_bid:.3f}"
                    elif exit_strategy == "below_entry":
                        if ride_bid is not None and ride_bid <= ride_entry - drop_below_entry_cents:
                            exit_now = True; exit_reason = f"below_entry_{ride_bid:.3f}"
                        elif ride_bid is not None and ride_bid >= ride_tp_threshold:
                            exit_now = True; exit_reason = f"tp_{ride_bid:.3f}"
                    elif exit_strategy == "trailing":
                        if ride_peak_bid > 0 and ride_bid is not None \
                                and ride_bid <= ride_peak_bid - trail_cents:
                            exit_now = True; exit_reason = f"trail_{ride_peak_bid:.3f}→{ride_bid:.3f}"
                        elif ride_bid is not None and ride_bid >= ride_tp_threshold:
                            exit_now = True; exit_reason = f"tp_{ride_bid:.3f}"

                    if exit_now:
                        if ride_side == "YES":
                            yes_status, yes_exit_px, yes_exit_reason = "ride_exit", ride_bid, exit_reason
                        else:
                            no_status, no_exit_px, no_exit_reason = "ride_exit", ride_bid, exit_reason

            if yes_status != "open" and no_status != "open":
                break

            # max hold safety
            if (ns - entry_ns) / 60e9 >= SCALP_MAX_HOLD_MIN:
                if yes_status == "open":
                    yes_status, yes_exit_px, yes_exit_reason = "max_hold", yb if yb else 0.0, "max_hold"
                if no_status == "open":
                    no_status, no_exit_px, no_exit_reason = "max_hold", nb if nb else 0.0, "max_hold"
                break

        # data_end fallback
        if yes_status == "open":
            last = next((t["best_bid"] for t in reversed(yes_arr) if t["best_bid"] is not None), 0.0)
            yes_status, yes_exit_px, yes_exit_reason = "data_end", last, "data_end"
        if no_status == "open":
            last = next((t["best_bid"] for t in reversed(no_arr) if t["best_bid"] is not None), 0.0)
            no_status, no_exit_px, no_exit_reason = "data_end", last, "data_end"

        pnl = yes_shares * yes_exit_px + no_shares * no_exit_px - 2 * SCALP_STAKE_USD
        trades.append({
            "match_id": mid, "pnl": pnl,
            "entry_y": entry_y, "entry_n": entry_n,
            "yes_exit": yes_exit_reason, "no_exit": no_exit_reason,
            "ride_side": ride_side, "ride_peak": ride_peak_bid,
        })

    return trades


def summarize(trades, label):
    if not trades: return f"  {label}: 0"
    pnls = sorted(t["pnl"] for t in trades)
    n = len(pnls); w = sum(1 for p in pnls if p > 0)
    return (f"  {label:<48} n={n:>3} ${sum(pnls):+8.2f} ${sum(pnls)/n:+.3f}/t "
            f"win={100*w/n:>3.0f}% med=${pnls[n//2]:+.3f} max=${pnls[-1]:+.2f} min=${pnls[0]:+.2f}")


def main():
    print("Loading data_v2 ...")
    mapping = load_mapping()
    snaps, league_of = load_snapshots_with_league()
    yes_tokens = {info["yes"] for info in mapping.values()}
    no_tokens = {info["no"] for info in mapping.values()}
    book = load_book(yes_tokens | no_tokens)
    print(f"  {len(mapping)} mapped markets, {len(book)} assets\n")

    print("=" * 78)
    print("RIDE-LEG EXIT STRATEGIES")
    print("=" * 78)

    # Baseline (reference)
    trades = run_scalp_with_ride_exit(mapping, snaps, book, exit_strategy="baseline")
    print(summarize(trades, "baseline (ride to 0.90, no stop)"))

    # Immediate sell
    trades = run_scalp_with_ride_exit(mapping, snaps, book, exit_strategy="immediate")
    print(summarize(trades, "immediate (sell ride at first tick after other scratch)"))

    # Delayed sells
    print()
    for d in [1, 2, 5, 10, 15, 30]:
        trades = run_scalp_with_ride_exit(mapping, snaps, book,
                                            exit_strategy="delayed", delay_min=d)
        print(summarize(trades, f"delayed_{d}min"))

    # Sell when bid drops below entry
    print()
    for drop in [0.0, 0.02, 0.05, 0.10, 0.15]:
        trades = run_scalp_with_ride_exit(mapping, snaps, book,
                                            exit_strategy="below_entry",
                                            drop_below_entry_cents=drop)
        print(summarize(trades, f"sell_when_bid_drops_{int(drop*100)}c_below_entry"))

    # Trailing stop
    print()
    for trail in [0.02, 0.03, 0.05, 0.08, 0.10]:
        trades = run_scalp_with_ride_exit(mapping, snaps, book,
                                            exit_strategy="trailing", trail_cents=trail)
        print(summarize(trades, f"trailing_{int(trail*100)}c"))

    # Combined: immediate sell on losers but try to ride winners — needs a
    # signal of who's winning. Use direction of first 30s mid move after entry.
    print("\n" + "=" * 78)
    print("BEST CANDIDATES PER STRATEGY FAMILY")
    print("=" * 78)
    candidates = [
        ("baseline (ride to 0.90)",
         dict(exit_strategy="baseline")),
        ("immediate (sell on other's scratch)",
         dict(exit_strategy="immediate")),
        ("delayed 2min",
         dict(exit_strategy="delayed", delay_min=2)),
        ("sell when bid drops 5c below entry",
         dict(exit_strategy="below_entry", drop_below_entry_cents=0.05)),
        ("trailing 3c",
         dict(exit_strategy="trailing", trail_cents=0.03)),
    ]
    for label, kwargs in candidates:
        trades = run_scalp_with_ride_exit(mapping, snaps, book, **kwargs)
        # Per-tournament breakdown for the best one
        if "immediate" in label:
            by_t = defaultdict(list)
            for t in trades:
                lg = league_of.get(t["match_id"], "0")
                by_t[lg].append(t)
            print(f"\n  {label}:")
            for lg, sub in sorted(by_t.items(), key=lambda x: -len(x[1])):
                name = {"19696":"DreamLeague","19101":"BLAST","19742":"tier-3"}.get(lg, f"other({lg})")
                print(summarize(sub, f"    {name}"))
        else:
            print(summarize(trades, label))


if __name__ == "__main__":
    main()
