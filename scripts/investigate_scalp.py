#!/usr/bin/env python3
"""Investigate the scalp strategy on data_v2.

Questions:
  1. What's the distribution of ride_peak_bid? (How close did losing
     rides come to a take-profit?)
  2. Does a hard stop-loss on the ride leg flip the strategy positive?
  3. What if the ride take-profit threshold is lower (0.70 or 0.80)?
  4. Does the +$0.05 "lock-in" exit help on the ride leg?
  5. Is there a tournament where scalp works?
  6. Does pre-game-only entry help vs early-game entry?
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

# Strategy params (mirrored, then varied below)
SCALP_STAKE_USD = 10.0
SCALP_MIN_PRICE = 0.40
SCALP_MAX_PRICE = 0.60
SCALP_MAX_SKEW = 0.08
SCALP_MAX_SUM = 1.03
SCALP_MIN_BID_SIZE_USD = 100.0
SCALP_MAX_BOOK_SPREAD = 0.04
SCALP_SCRATCH_CENTS = 0.02
SCALP_MAX_HOLD_MIN = 90.0

# Tournament labels (from earlier per-league analysis)
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
    } for m in mk["markets"]
        if m.get("dota_match_id") and m["dota_match_id"].isdigit() and m["dota_match_id"]!="123"}


def load_snapshots_with_league():
    ds = pds.dataset(REPO_ROOT/"data_v2"/"snapshots", format="parquet", partitioning="hive")
    t = ds.to_table(columns=["match_id","received_at_ns","game_time_sec","league_id"])
    by_match = defaultdict(list)
    league_of = {}
    for i in range(t.num_rows):
        mid = t["match_id"][i].as_py()
        by_match[mid].append((t["received_at_ns"][i].as_py(), t["game_time_sec"][i].as_py()))
        league_of[mid] = t["league_id"][i].as_py()
    for mid in by_match: by_match[mid].sort()
    return by_match, league_of


def load_book(tokens):
    ds = pds.dataset(REPO_ROOT/"data_v2"/"book_ticks", format="parquet", partitioning="hive")
    t = ds.to_table(columns=["asset_id","received_at_ns","best_bid","best_ask","ask_size","bid_size"],
                    filter=pc.is_in(pc.field("asset_id"), pa.array(list(tokens))))
    by = defaultdict(list)
    for i in range(t.num_rows):
        by[t["asset_id"][i].as_py()].append({
            "received_at_ns": t["received_at_ns"][i].as_py(),
            "best_bid": t["best_bid"][i].as_py(),
            "best_ask": t["best_ask"][i].as_py(),
            "ask_size": t["ask_size"][i].as_py(),
            "bid_size": t["bid_size"][i].as_py(),
        })
    for a in by: by[a].sort(key=lambda x: x["received_at_ns"])
    return by


def game_time_at(snap_list, ns):
    if not snap_list: return None
    times = [s[0] for s in snap_list]
    i = bisect.bisect_right(times, ns) - 1
    if i < 0: return None
    return snap_list[i][1]


def qualifies(*, yes_book, no_book, game_time_sec, max_game_time_sec):
    ya = yes_book.get("best_ask"); na = no_book.get("best_ask")
    if ya is None or na is None: return False
    if not (SCALP_MIN_PRICE <= ya <= SCALP_MAX_PRICE): return False
    if not (SCALP_MIN_PRICE <= na <= SCALP_MAX_PRICE): return False
    if game_time_sec is not None and game_time_sec >= max_game_time_sec: return False
    if abs(ya - na) > SCALP_MAX_SKEW: return False
    if ya + na > SCALP_MAX_SUM: return False
    for book in (yes_book, no_book):
        bid = book.get("best_bid"); ask = book.get("best_ask")
        bid_size = book.get("bid_size") or 0
        if bid is not None and ask is not None and (ask - bid) > SCALP_MAX_BOOK_SPREAD:
            return False
        if bid is not None and bid_size and (bid * bid_size) < SCALP_MIN_BID_SIZE_USD:
            return False
    return True


def run_scalp(
    mapping, snaps, book,
    *,
    ride_tp_threshold: float = 0.90,
    ride_hard_stop: float | None = None,    # absolute floor for ride leg bid; None = original (no stop on ride)
    ride_lock_in_cents: float | None = None, # exit ride at entry + this many cents
    max_game_time_sec: int = 1800,
    league_filter: str | None = None,
    log_ride_peaks: bool = False,
):
    """Run the scalp strategy with parameterized exits."""
    trades = []
    ride_peaks_logged = []

    for mid, info in mapping.items():
        if league_filter is not None:
            # League data comes from snapshots; check first
            pass
        yes_arr = book.get(info["yes"]); no_arr = book.get(info["no"])
        if not yes_arr or not no_arr: continue
        snap_list = snaps.get(mid, [])
        no_times = [n["received_at_ns"] for n in no_arr]

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
        yes_status, no_status = "open", "open"
        yes_exit_px = no_exit_px = 0.0
        yes_exit_reason = no_exit_reason = ""
        ride_side = None
        ride_peak_bid = 0.0

        # Walk merged ticks
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

            # Scratch checks (both sides, before ride assignment)
            for leg_status, status_name, entry, bid in (
                (yes_status, "yes", entry_y, yb),
                (no_status,  "no",  entry_n, nb),
            ):
                pass  # handled inline below for clarity
            if yes_status == "open" and yb is not None and yb >= entry_y + SCALP_SCRATCH_CENTS:
                yes_status, yes_exit_px, yes_exit_reason = "scratched", yb, "scratched"
            if no_status == "open" and nb is not None and nb >= entry_n + SCALP_SCRATCH_CENTS:
                no_status, no_exit_px, no_exit_reason = "scratched", nb, "scratched"

            # Determine ride
            if ride_side is None:
                y_done = yes_status != "open"
                n_done = no_status != "open"
                if y_done and not n_done: ride_side = "NO"
                elif n_done and not y_done: ride_side = "YES"
                elif y_done and n_done:
                    break

            if ride_side == "YES" and yes_status == "open":
                if yb is not None and yb > ride_peak_bid: ride_peak_bid = yb
                # exit checks on ride leg
                if yb is not None and yb >= ride_tp_threshold:
                    yes_status, yes_exit_px, yes_exit_reason = "ride_tp", yb, f"ride_tp_{yb:.3f}"
                elif ride_lock_in_cents is not None and yb is not None and yb >= entry_y + ride_lock_in_cents:
                    yes_status, yes_exit_px, yes_exit_reason = "ride_lock", yb, f"ride_lock_{yb:.3f}"
                elif ride_hard_stop is not None and yb is not None and yb <= ride_hard_stop:
                    yes_status, yes_exit_px, yes_exit_reason = "ride_stop", yb, f"ride_stop_{yb:.3f}"
            elif ride_side == "NO" and no_status == "open":
                if nb is not None and nb > ride_peak_bid: ride_peak_bid = nb
                if nb is not None and nb >= ride_tp_threshold:
                    no_status, no_exit_px, no_exit_reason = "ride_tp", nb, f"ride_tp_{nb:.3f}"
                elif ride_lock_in_cents is not None and nb is not None and nb >= entry_n + ride_lock_in_cents:
                    no_status, no_exit_px, no_exit_reason = "ride_lock", nb, f"ride_lock_{nb:.3f}"
                elif ride_hard_stop is not None and nb is not None and nb <= ride_hard_stop:
                    no_status, no_exit_px, no_exit_reason = "ride_stop", nb, f"ride_stop_{nb:.3f}"

            if yes_status != "open" and no_status != "open":
                break

            # max hold
            age_min = (ns - entry_ns) / 60e9
            if age_min >= SCALP_MAX_HOLD_MIN:
                for nm, st_name, bid in (("yes", yes_status, yb), ("no", no_status, nb)):
                    pass
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
            "yes_exit_px": yes_exit_px, "no_exit_px": no_exit_px,
            "ride_side": ride_side, "ride_peak_bid": ride_peak_bid,
        })
        if log_ride_peaks and ride_side is not None:
            ride_peaks_logged.append({"match_id": mid, "ride_side": ride_side,
                                       "peak": ride_peak_bid, "pnl": pnl})

    return trades, ride_peaks_logged


def summarize_short(trades, label):
    if not trades: return f"  {label}: 0"
    pnls = sorted(t["pnl"] for t in trades)
    n = len(pnls); w = sum(1 for p in pnls if p > 0)
    total = sum(pnls)
    return (f"  {label:<46} n={n:>3} ${total:+8.2f} ${total/n:+.3f}/t "
            f"win={100*w/n:>3.0f}% med=${pnls[n//2]:+.3f} max=${pnls[-1]:+.2f} min=${pnls[0]:+.2f}")


def main():
    print("Loading data_v2 ...")
    mapping = load_mapping()
    snaps, league_of = load_snapshots_with_league()
    yes_tokens = {info["yes"] for info in mapping.values()}
    no_tokens = {info["no"] for info in mapping.values()}
    book = load_book(yes_tokens | no_tokens)
    print(f"  {len(mapping)} mapped markets, {len(book)} assets in book.\n")

    # --- A. Baseline (current production config) ---
    print("=" * 78)
    print("A. BASELINE — current production scalp config")
    print("=" * 78)
    baseline, ride_peaks = run_scalp(mapping, snaps, book, log_ride_peaks=True)
    print(summarize_short(baseline, "baseline"))

    print("\nRide-leg max-bid distribution (when one leg scratched and other rode):")
    print(f"  ride opportunities: {len(ride_peaks)}")
    if ride_peaks:
        peaks = sorted(p["peak"] for p in ride_peaks)
        m = len(peaks)
        print(f"  peak distribution: p25={peaks[m//4]:.3f} median={peaks[m//2]:.3f} "
              f"p75={peaks[3*m//4]:.3f} max={peaks[-1]:.3f}")
        for lo, hi in [(0,0.55),(0.55,0.60),(0.60,0.65),(0.65,0.75),(0.75,0.90),(0.90,1.01)]:
            c = sum(1 for p in peaks if lo <= p < hi)
            print(f"    {lo:.2f}–{hi:.2f}: {c}")

    # --- B. Lower ride take-profit ---
    print("\n" + "=" * 78)
    print("B. LOWER RIDE TAKE-PROFIT")
    print("=" * 78)
    for tp in [0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.90]:
        trades, _ = run_scalp(mapping, snaps, book, ride_tp_threshold=tp)
        print(summarize_short(trades, f"ride_tp={tp:.2f}"))

    # --- C. Hard stop-loss on ride leg ---
    print("\n" + "=" * 78)
    print("C. HARD STOP ON RIDE LEG (in addition to default 0.90 TP)")
    print("=" * 78)
    for stop in [0.10, 0.20, 0.30, 0.40, 0.50]:
        trades, _ = run_scalp(mapping, snaps, book, ride_hard_stop=stop)
        print(summarize_short(trades, f"ride_hard_stop=${stop:.2f}"))

    # --- D. Lock-in profit on ride leg ---
    print("\n" + "=" * 78)
    print("D. LOCK-IN EXIT ON RIDE LEG (exit at entry + Nc)")
    print("=" * 78)
    for lock in [0.03, 0.05, 0.08, 0.10, 0.15]:
        trades, _ = run_scalp(mapping, snaps, book, ride_lock_in_cents=lock)
        print(summarize_short(trades, f"ride_lock=+{int(lock*100)}c"))

    # --- E. Combined: lower TP + hard stop ---
    print("\n" + "=" * 78)
    print("E. COMBINED — lower take-profit AND hard stop on ride leg")
    print("=" * 78)
    for tp in [0.65, 0.70]:
        for stop in [0.20, 0.30, 0.40]:
            trades, _ = run_scalp(mapping, snaps, book,
                                   ride_tp_threshold=tp, ride_hard_stop=stop)
            print(summarize_short(trades, f"tp={tp:.2f} stop=${stop:.2f}"))

    # --- F. Per-tournament breakdown of the best variant ---
    print("\n" + "=" * 78)
    print("F. BEST-VARIANT BY TOURNAMENT")
    print("=" * 78)
    # Pick the best combined variant: tp=0.65 + stop=0.30
    trades_best, _ = run_scalp(mapping, snaps, book,
                                ride_tp_threshold=0.65, ride_hard_stop=0.30)
    by_tournament = defaultdict(list)
    for t in trades_best:
        lg = league_of.get(t["match_id"], "0")
        name = TOURNAMENT_NAMES.get(str(lg), f"other ({lg})")
        by_tournament[name].append(t)
    print("  Variant: tp=0.65, stop=$0.30")
    for name, sub in by_tournament.items():
        print(summarize_short(sub, f"  {name}"))

    # --- G. Pre-game-only entry ---
    print("\n" + "=" * 78)
    print("G. EARLIER ENTRY CUTOFF")
    print("=" * 78)
    for cutoff in [120, 300, 600, 1200, 1800]:
        trades, _ = run_scalp(mapping, snaps, book,
                               max_game_time_sec=cutoff,
                               ride_tp_threshold=0.65, ride_hard_stop=0.30)
        print(summarize_short(trades, f"max_game_time={cutoff}s ({cutoff//60}min)"))


if __name__ == "__main__":
    main()
