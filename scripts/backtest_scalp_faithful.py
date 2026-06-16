#!/usr/bin/env python3
"""Faithful scalp backtest.

Replays the scalp pair lifecycle from scalp_executor.py against real book
tick streams in data_v2. Tracks JOINT state across both legs (the previous
sim walked them independently, which is wrong — only one leg can be the
"ride" at any time).

Mechanics (matching scalp_executor.on_book_tick):
  1. Enter when qualifies() passes on a synced YES/NO tick.
  2. Both legs are unscratched. On each subsequent tick:
     - If any unscratched leg's bid >= entry_ask + 0.02: scratch it.
     - If any unscratched, un-rided leg's bid <= entry - stop_loss: stop it.
  3. Once exactly one leg is closed (scratched or stopped), the OTHER becomes
     the "ride". The ride stops being a scratch candidate.
  4. Ride leg exit when:
     - ride_bid >= 0.90 (take profit)
     - ride_peak crosses 0.60, then drops trail_cents below peak (trailing stop)
     - max hold time elapsed
     - final tick reached (settle proxy)
  5. PnL = scratch_proceeds + ride_proceeds + stopped_proceeds − total_cost
"""
from __future__ import annotations

import bisect
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import yaml
import pyarrow as pa
import pyarrow.dataset as pds
import pyarrow.compute as pc

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Mirror scalp_executor defaults so the sim matches production.
SCALP_STAKE_USD = 10.0
SCALP_MIN_PRICE = 0.40
SCALP_MAX_PRICE = 0.60
SCALP_MAX_SKEW = 0.08
SCALP_MAX_SUM = 1.03
SCALP_MIN_BID_SIZE_USD = 100.0
SCALP_MAX_BOOK_SPREAD = 0.04
SCALP_SCRATCH_CENTS = 0.02
SCALP_STOP_LOSS_CENTS = 0.25
SCALP_RIDE_TARGET = 0.90
SCALP_RIDE_TRAIL_CENTS = 0.10
SCALP_RIDE_TRAIL_MIN_PEAK = 0.60
SCALP_MAX_HOLD_MIN = 90.0
SCALP_CROSS_BOOK_DISAGREE_MAX = 0.02
SCALP_MAX_GAME_TIME_SEC = 1800

# Mid-game stricter gates
SCALP_EARLY_GAME_SEC = 600
SCALP_MID_GAME_MAX_SUM = 1.00
SCALP_MID_GAME_MAX_SKEW = 0.05
SCALP_MID_GAME_MIN_BID_USD = 200.0


# ---------- loaders (shared with the other backtests) ----------
def load_mapping():
    with open(REPO_ROOT / "markets.yaml") as f:
        mk = yaml.safe_load(f)
    return {m["dota_match_id"]: {
        "yes": m["yes_token_id"], "no": m["no_token_id"],
        "name": m.get("name", ""),
        "market_id": m.get("market_id", ""),
    } for m in mk["markets"]
        if m.get("dota_match_id") and m["dota_match_id"].isdigit() and m["dota_match_id"]!="123"}


def load_snapshots():
    """Load snapshots to determine per-match game_time at each book tick."""
    ds = pds.dataset(REPO_ROOT/"data_v2"/"snapshots", format="parquet", partitioning="hive")
    t = ds.to_table(columns=["match_id","received_at_ns","game_time_sec"])
    by_match = defaultdict(list)
    for i in range(t.num_rows):
        by_match[t["match_id"][i].as_py()].append(
            (t["received_at_ns"][i].as_py(), t["game_time_sec"][i].as_py())
        )
    for mid in by_match:
        by_match[mid].sort()
    return by_match


def load_book_full(tokens):
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
    """Return the game_time_sec at the latest snapshot at-or-before ns."""
    if not snap_list: return None
    times = [s[0] for s in snap_list]
    i = bisect.bisect_right(times, ns) - 1
    if i < 0: return None
    return snap_list[i][1]


# ---------- qualifies (faithful reproduction) ----------
def qualifies(*, yes_book, no_book, game_time_sec):
    """Returns (ok, reason)."""
    ya = yes_book.get("best_ask")
    na = no_book.get("best_ask")
    if ya is None or na is None: return False, "missing_ask"
    if not (SCALP_MIN_PRICE <= ya <= SCALP_MAX_PRICE): return False, "yes_price_out_of_range"
    if not (SCALP_MIN_PRICE <= na <= SCALP_MAX_PRICE): return False, "no_price_out_of_range"
    if game_time_sec is not None and game_time_sec >= SCALP_MAX_GAME_TIME_SEC:
        return False, "too_late"
    in_mid = game_time_sec is not None and game_time_sec >= SCALP_EARLY_GAME_SEC
    max_skew = SCALP_MID_GAME_MAX_SKEW if in_mid else SCALP_MAX_SKEW
    max_sum  = SCALP_MID_GAME_MAX_SUM  if in_mid else SCALP_MAX_SUM
    min_bid_usd = SCALP_MID_GAME_MIN_BID_USD if in_mid else SCALP_MIN_BID_SIZE_USD
    if abs(ya - na) > max_skew: return False, "skew"
    if ya + na > max_sum: return False, "sum"
    for book in (yes_book, no_book):
        bid = book.get("best_bid"); ask = book.get("best_ask")
        bid_size = book.get("bid_size") or 0
        if bid is not None and ask is not None and (ask - bid) > SCALP_MAX_BOOK_SPREAD:
            return False, "spread_too_wide"
        if bid is not None and bid_size and (bid * bid_size) < min_bid_usd:
            return False, "depth_too_thin"
    # SC-1 cross-book disagreement gate
    y_bid = yes_book.get("best_bid"); n_bid = no_book.get("best_bid")
    y_mid = (y_bid + ya) / 2 if y_bid is not None else ya
    n_mid = (n_bid + na) / 2 if n_bid is not None else na
    if abs(y_mid - (1.0 - n_mid)) > SCALP_CROSS_BOOK_DISAGREE_MAX:
        return False, "cross_book_disagreement"
    return True, ""


# ---------- pair lifecycle ----------
class Leg:
    __slots__ = ("entry_ask", "shares", "status", "exit_px", "exit_reason")
    def __init__(self, entry_ask, shares):
        self.entry_ask = entry_ask
        self.shares = shares
        self.status = "open"
        self.exit_px = 0.0
        self.exit_reason = ""

    def proceeds_usd(self) -> float:
        return self.shares * self.exit_px


class Pair:
    def __init__(self, *, yes_entry_ask, no_entry_ask, opened_ns, match_id):
        self.yes = Leg(yes_entry_ask, SCALP_STAKE_USD / yes_entry_ask)
        self.no  = Leg(no_entry_ask,  SCALP_STAKE_USD / no_entry_ask)
        self.opened_ns = opened_ns
        self.match_id = match_id
        self.ride_token: str | None = None
        self.ride_peak_bid: float = 0.0
        self.ride_trail_armed = False
        self.closed = False
        self.close_reason = ""

    def cost_usd(self) -> float:
        return SCALP_STAKE_USD * 2

    def total_pnl(self) -> float:
        return (self.yes.proceeds_usd() + self.no.proceeds_usd()) - self.cost_usd()


def step_pair(pair: Pair, *, yes_bid, no_bid, now_ns):
    """One tick of the joint-state machine. Returns True if the pair is closed."""
    # 1. Scratch checks on still-open legs (independent of ride state).
    for leg, bid in ((pair.yes, yes_bid), (pair.no, no_bid)):
        if leg.status != "open" or bid is None: continue
        scratch_px = leg.entry_ask + SCALP_SCRATCH_CENTS
        if bid >= scratch_px:
            leg.status = "scratched"; leg.exit_px = bid; leg.exit_reason = "scratched"

    # 2. Identify ride if exactly one leg has closed
    if pair.ride_token is None:
        yes_done = pair.yes.status != "open"
        no_done  = pair.no.status  != "open"
        if yes_done and not no_done:
            pair.ride_token = "NO"
        elif no_done and not yes_done:
            pair.ride_token = "YES"
        elif yes_done and no_done:
            # Both scratched simultaneously — done.
            pair.closed = True; pair.close_reason = "both_scratched"
            return True

    # 3. Stop loss on un-rided open legs (only fires before ride is set or on
    # the non-ride leg). In practice once ride_token is set, the ride leg is
    # the only open leg and its exits are handled below.
    for leg, bid in ((pair.yes, yes_bid), (pair.no, no_bid)):
        if leg.status != "open" or bid is None: continue
        # If a leg is selected as the ride, skip stop-loss for it
        is_ride = (pair.ride_token == "YES" and leg is pair.yes) or \
                  (pair.ride_token == "NO" and leg is pair.no)
        if is_ride: continue
        stop_px = leg.entry_ask - SCALP_STOP_LOSS_CENTS
        if bid <= stop_px:
            leg.status = "stopped"; leg.exit_px = bid; leg.exit_reason = "stopped"

    # 4. Ride exit logic
    if pair.ride_token is not None:
        ride_leg = pair.yes if pair.ride_token == "YES" else pair.no
        ride_bid = yes_bid if pair.ride_token == "YES" else no_bid
        if ride_leg.status == "open" and ride_bid is not None:
            if ride_bid > pair.ride_peak_bid:
                pair.ride_peak_bid = ride_bid
            if pair.ride_peak_bid >= SCALP_RIDE_TRAIL_MIN_PEAK:
                pair.ride_trail_armed = True
            if ride_bid >= SCALP_RIDE_TARGET:
                ride_leg.status = "ride_tp"; ride_leg.exit_px = ride_bid; ride_leg.exit_reason = f"ride_tp_{ride_bid:.3f}"
            elif pair.ride_trail_armed and ride_bid <= pair.ride_peak_bid - SCALP_RIDE_TRAIL_CENTS:
                ride_leg.status = "ride_trail"; ride_leg.exit_px = ride_bid; ride_leg.exit_reason = f"ride_trail_{pair.ride_peak_bid:.3f}→{ride_bid:.3f}"

    # 5. Max hold time
    age_min = (now_ns - pair.opened_ns) / 60e9
    if age_min >= SCALP_MAX_HOLD_MIN:
        for leg, bid in ((pair.yes, yes_bid), (pair.no, no_bid)):
            if leg.status == "open":
                leg.status = "max_hold"; leg.exit_px = bid if bid is not None else 0.0
                leg.exit_reason = "max_hold"
        pair.closed = True; pair.close_reason = "max_hold"
        return True

    if pair.yes.status != "open" and pair.no.status != "open":
        pair.closed = True
        pair.close_reason = (pair.yes.exit_reason if "ride" in pair.yes.exit_reason or "scratch" in pair.yes.exit_reason
                             else pair.no.exit_reason)
        return True
    return False


# ---------- backtest driver ----------
def backtest(mapping, snaps, book):
    """For each market, find the first qualifying entry, then run step_pair
    on every subsequent synced book tick."""
    trades = []
    skip_counter = Counter()

    for mid, info in mapping.items():
        yes_arr = book.get(info["yes"])
        no_arr  = book.get(info["no"])
        if not yes_arr or not no_arr:
            skip_counter["no_book_data"] += 1
            continue
        snap_list = snaps.get(mid, [])

        # Merge tick streams (keep all timestamps, pick the most recent book
        # state per side).
        no_times = [n["received_at_ns"] for n in no_arr]

        # Find first qualifying entry
        entry_idx = None
        for i, y in enumerate(yes_arr):
            ns = y["received_at_ns"]
            j = bisect.bisect_right(no_times, ns) - 1
            if j < 0: continue
            n = no_arr[j]
            if abs(ns - n["received_at_ns"]) > 2_000_000_000: continue
            gt = game_time_at(snap_list, ns)
            ok, reason = qualifies(yes_book=y, no_book=n, game_time_sec=gt)
            if ok:
                entry_idx = (i, j); entry_ns = ns
                entry_y, entry_n = y["best_ask"], n["best_ask"]
                break
            else:
                skip_counter[reason] += 1
        if entry_idx is None:
            continue

        # Open the pair
        pair = Pair(yes_entry_ask=entry_y, no_entry_ask=entry_n,
                    opened_ns=entry_ns, match_id=mid)
        # Walk all ticks after entry, picking the most recent state from each side
        i_y, i_n = entry_idx
        # Iterate over union of YES + NO ticks chronologically
        events = []
        for y in yes_arr[i_y + 1:]:
            events.append(("Y", y["received_at_ns"], y))
        for n in no_arr[i_n + 1:]:
            events.append(("N", n["received_at_ns"], n))
        events.sort(key=lambda x: x[1])
        cur_y, cur_n = yes_arr[i_y], no_arr[i_n]
        for side, ns, tick in events:
            if side == "Y": cur_y = tick
            else: cur_n = tick
            yb = cur_y.get("best_bid"); nb = cur_n.get("best_bid")
            done = step_pair(pair, yes_bid=yb, no_bid=nb, now_ns=ns)
            if done: break

        # If pair is still open at end of data, force-close at final bids.
        if not pair.closed:
            for leg, last_bid_arr in ((pair.yes, yes_arr), (pair.no, no_arr)):
                if leg.status == "open":
                    last_bid = next((t["best_bid"] for t in reversed(last_bid_arr)
                                       if t["best_bid"] is not None), 0.0)
                    leg.status = "settle_proxy"; leg.exit_px = last_bid
                    leg.exit_reason = "settle_proxy"
            pair.closed = True; pair.close_reason = "data_end"

        last_event_ns = events[-1][1] if events else entry_ns
        trades.append({
            "match_id": mid, "entry_ns": entry_ns,
            "yes_entry_ask": entry_y, "no_entry_ask": entry_n,
            "yes_exit": pair.yes.exit_reason, "no_exit": pair.no.exit_reason,
            "yes_exit_px": pair.yes.exit_px, "no_exit_px": pair.no.exit_px,
            "pnl": pair.total_pnl(),
            "cost": pair.cost_usd(),
            "close_reason": pair.close_reason,
            "duration_sec": (last_event_ns - entry_ns) / 1e9,
            "ride_token": pair.ride_token, "ride_peak": pair.ride_peak_bid,
        })

    return trades, skip_counter


def summarize(trades):
    if not trades: return "no trades"
    pnls = sorted(t["pnl"] for t in trades)
    n = len(pnls); wins = sum(1 for p in pnls if p > 0)
    total = sum(pnls)
    total_cost = sum(t["cost"] for t in trades)
    return (f"  trades:        {n:,}\n"
            f"  total PnL:     ${total:+.2f}\n"
            f"  $/trade:       ${total/n:+.4f}\n"
            f"  win rate:      {100*wins/n:.1f}%\n"
            f"  median:        ${pnls[n//2]:+.4f}\n"
            f"  p25 / p75:     ${pnls[n//4]:+.4f} / ${pnls[3*n//4]:+.4f}\n"
            f"  total deployed: ${total_cost:.2f}\n"
            f"  ROI:           {100*total/max(total_cost,1):.2f}%")


def main():
    t0 = time.time()
    mapping = load_mapping()
    snaps = load_snapshots()
    print(f"Loaded {len(snaps)} matches with snapshots ({time.time()-t0:.2f}s)")
    yes_tokens = {info["yes"] for info in mapping.values()}
    no_tokens = {info["no"] for info in mapping.values()}
    t0 = time.time()
    book = load_book_full(yes_tokens | no_tokens)
    print(f"Loaded book for {len(book)} assets ({time.time()-t0:.2f}s)")

    print("\n=== FAITHFUL SCALP BACKTEST ===\n")
    t0 = time.time()
    trades, skip_counter = backtest(mapping, snaps, book)
    print(f"(walked in {time.time()-t0:.2f}s)")
    print(summarize(trades))

    print("\nClose reasons:")
    for r, n in Counter(t["close_reason"] for t in trades).most_common():
        print(f"  {r:<22} {n}")

    print("\nRide outcomes (the leg held after first scratch/stop):")
    print(f"  ride_tp     (hit 0.90):       {sum(1 for t in trades if 'ride_tp' in t['yes_exit'] or 'ride_tp' in t['no_exit'])}")
    print(f"  ride_trail  (trailing stop):  {sum(1 for t in trades if 'ride_trail' in t['yes_exit'] or 'ride_trail' in t['no_exit'])}")
    print(f"  stopped     (lost via stop):  {sum(1 for t in trades if t['yes_exit']=='stopped' or t['no_exit']=='stopped')}")
    print(f"  settle_proxy (data ended):    {sum(1 for t in trades if 'settle' in t['yes_exit'] or 'settle' in t['no_exit'])}")

    print("\nTop entry-rejection reasons (markets that never qualified):")
    for r, n in skip_counter.most_common(10):
        print(f"  {r:<28} {n}")

    print("\nSample trades (sorted by PnL):")
    for t in sorted(trades, key=lambda x: x['pnl'])[:3]:
        print(f"  {t['match_id']}  $/{t['pnl']:+.2f}  entry y={t['yes_entry_ask']:.2f} n={t['no_entry_ask']:.2f}  "
              f"y_exit={t['yes_exit'][:18]}@{t['yes_exit_px']:.3f}  n_exit={t['no_exit'][:18]}@{t['no_exit_px']:.3f}  "
              f"dur={t['duration_sec']/60:.0f}min")
    print("  ---")
    for t in sorted(trades, key=lambda x: -x['pnl'])[:3]:
        print(f"  {t['match_id']}  $/{t['pnl']:+.2f}  entry y={t['yes_entry_ask']:.2f} n={t['no_entry_ask']:.2f}  "
              f"y_exit={t['yes_exit'][:18]}@{t['yes_exit_px']:.3f}  n_exit={t['no_exit'][:18]}@{t['no_exit_px']:.3f}  "
              f"dur={t['duration_sec']/60:.0f}min")


if __name__ == "__main__":
    main()
