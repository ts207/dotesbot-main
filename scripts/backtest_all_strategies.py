#!/usr/bin/env python3
"""Multi-strategy backtest on data_v2.

Strategies:
  1. Continuous — re-run the scorer replay
  2. Arb        — scan every synced YES+NO tick pair for YES_ask+NO_ask < threshold
  3. Scalp      — simulate the scratch+ride lifecycle on pre-game windows

Outputs:
  - per-strategy: trade count, total PnL, $/trade, win rate
  - capital usage profile
  - combined projection across all three (assuming independent execution)
"""
from __future__ import annotations

import bisect
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml
import pyarrow as pa
import pyarrow.dataset as pds
import pyarrow.compute as pc

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from continuous_scorer import score_snapshot, ContinuousSignal, ScoreReject
from arb_scanner import scan_pair, ArbOpportunity, ArbReject


# Common parameters.
COST_HALF_SPREAD = 0.005       # baked into continuous PnL
ARB_CAPITAL_PER_ARB = 10.0     # total $ deployed per arb (matched-shares)
ARB_MIN_PROFIT_CENTS = 1.5

# Scalp parameters mirroring scalp_executor defaults.
SCALP_STAKE_USD = 10.0
SCALP_MIN_PRICE = 0.40
SCALP_MAX_PRICE = 0.60
SCALP_MAX_SKEW = 0.08
SCALP_MAX_SUM = 1.03
SCALP_SCRATCH_CENTS = 0.02
SCALP_STOP_LOSS_CENTS = 0.25
SCALP_RIDE_TARGET = 0.90
SCALP_MAX_GAME_TIME_SEC = 1800


# ---------- shared loaders ----------
def load_mapping():
    with open(REPO_ROOT / "markets.yaml") as f:
        mk = yaml.safe_load(f)
    return {m["dota_match_id"]: {
        "yes": m["yes_token_id"], "no": m["no_token_id"],
        "name": m.get("name", ""),
        "side": m.get("steam_side_mapping", "normal"),
        "market_id": m.get("market_id", ""),
    } for m in mk["markets"]
        if m.get("dota_match_id") and m["dota_match_id"].isdigit() and m["dota_match_id"]!="123"}


def load_snapshots():
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


def load_book_full(tokens):
    """Return per-asset list of (ns, bid, ask, mid, spread, bid_size, ask_size) tuples."""
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
            "spread": t["spread"][i].as_py(),
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


# ============================================================================
# Strategy 1: Continuous (re-uses replay logic)
# ============================================================================
def backtest_continuous(snaps, book, mapping):
    """Re-run scorer replay. Returns per-trade list and aggregate stats."""
    joined = [mid for mid in snaps if mid in mapping and mapping[mid]["yes"] in book and mapping[mid]["no"] in book]
    trades = []
    horizons_ns = 60 * 1_000_000_000

    for mid in joined:
        info = mapping[mid]
        yes_tok, no_tok = info["yes"], info["no"]
        yes_arr = book[yes_tok]
        pregame_yes_mid = next((b["mid"] for b in yes_arr if b["mid"] is not None), 0.5)
        srows = snaps[mid]

        for i in range(1, len(srows)):
            prev_snap, cur_snap = srows[i-1], srows[i]
            yes_b = book_at(book, yes_tok, cur_snap["received_at_ns"])
            no_b  = book_at(book, no_tok,  cur_snap["received_at_ns"])
            if not yes_b or not no_b:
                continue
            yes_with_mid = {**yes_b, "mid": yes_b["mid"] or (
                ((yes_b["best_bid"] or 0) + (yes_b["best_ask"] or 0)) / 2 if yes_b["best_bid"] and yes_b["best_ask"] else None)}
            no_with_mid  = {**no_b,  "mid": no_b["mid"]  or (
                ((no_b["best_bid"]  or 0) + (no_b["best_ask"]  or 0)) / 2 if no_b["best_bid"]  and no_b["best_ask"]  else None)}
            res = score_snapshot(
                prev_snap=prev_snap, cur_snap=cur_snap,
                yes_book=yes_with_mid, no_book=no_with_mid,
                pregame_yes_mid=pregame_yes_mid,
                mapping={"steam_side_mapping": info["side"]},
            )
            if not isinstance(res, ContinuousSignal):
                continue
            # Exit price at +60s
            target_tok = yes_tok if res.side == "YES" else no_tok
            entry_b = book_at(book, target_tok, res.received_at_ns)
            exit_b = book_at(book, target_tok, res.received_at_ns + horizons_ns)
            if not entry_b or not exit_b or entry_b["mid"] is None or exit_b["mid"] is None:
                continue
            d_mid = exit_b["mid"] - entry_b["mid"]
            ret_per_dollar = (d_mid - COST_HALF_SPREAD) / entry_b["mid"]
            pnl = ret_per_dollar * res.sized_usd
            trades.append({
                "strategy": "continuous", "match_id": res.match_id, "ns": res.received_at_ns,
                "pnl": pnl, "size_usd": res.sized_usd, "side": res.side,
            })
    return trades


# ============================================================================
# Strategy 2: Arb (independent scanner)
# ============================================================================
def backtest_arb(book, mapping, sync_window_ns: int = 2_000_000_000):
    """For each market, scan every YES tick — try to sync to a NO tick within
    `sync_window_ns`. When ArbOpportunity fires, simulate execution at ask
    and collect $1 at settlement.

    Cooldown: once we fire an arb on a market, skip until the price moves
    back above the threshold then back below (rough simulation of real-time
    open-arb tracking)."""
    trades = []
    capital_locked_usd_total = 0.0
    capital_seconds_locked = 0.0

    for mid, info in mapping.items():
        yes_arr = book.get(info["yes"])
        no_arr  = book.get(info["no"])
        if not yes_arr or not no_arr:
            continue
        no_times = [x["received_at_ns"] for x in no_arr]

        # Estimate match end as the last book tick we see for this match.
        match_end_ns = max(yes_arr[-1]["received_at_ns"], no_arr[-1]["received_at_ns"])

        had_arb = False  # cooldown flag — must come back above threshold to re-fire
        for y in yes_arr:
            ns = y["received_at_ns"]
            if y["best_ask"] is None: continue
            i = bisect.bisect_right(no_times, ns) - 1
            if i < 0: continue
            n = no_arr[i]
            if abs(ns - n["received_at_ns"]) > sync_window_ns: continue
            if n["best_ask"] is None: continue

            res = scan_pair(
                yes_book=y, no_book=n,
                mapping={
                    "market_id": info["market_id"], "dota_match_id": mid,
                    "yes_token_id": info["yes"], "no_token_id": info["no"],
                },
                received_at_ns=ns,
                total_capital_usd=ARB_CAPITAL_PER_ARB,
                min_profit_cents=ARB_MIN_PROFIT_CENTS,
            )
            if isinstance(res, ArbOpportunity):
                if had_arb:
                    continue  # waiting for re-arm
                # The scanner now computes matched-shares payout directly.
                arb_cost = res.arb_cost
                shares = res.shares_per_side
                cost_usd = res.total_capital_usd
                payout = shares * 1.0
                pnl = payout - cost_usd
                hold_sec = max((match_end_ns - ns) / 1e9, 0.1)
                capital_locked_usd_total += cost_usd
                capital_seconds_locked += cost_usd * hold_sec
                trades.append({
                    "strategy": "arb", "match_id": mid, "ns": ns,
                    "pnl": pnl, "cost_usd": cost_usd, "size_usd": cost_usd,
                    "arb_cost": arb_cost, "hold_sec": hold_sec,
                    "shares": shares, "payout": payout,
                })
                had_arb = True
            elif isinstance(res, ArbReject):
                if had_arb and res.reason == "below_min_profit":
                    # We're back above the threshold — re-arm for the next dip.
                    had_arb = False

    return trades, capital_locked_usd_total, capital_seconds_locked


# ============================================================================
# Strategy 3: Scalp (scratch + ride lifecycle simulation)
# ============================================================================
def backtest_scalp(book, mapping):
    """Simulate scalp entries during pre-kickoff window. Track scratch leg
    (exits at +2c bid) and ride leg (exits at 0.90 or game end).

    For each market:
      1. Find first pre-game/early-game window where qualifies() passes.
      2. Enter both legs at ask.
      3. Walk forward through book ticks tracking bid movements.
      4. Scratch the LOSING leg when its bid reaches entry - 2c (cut losers).
         Ride the WINNING leg until its bid reaches 0.90 or match ends.
    """
    trades = []
    capital_locked_usd_total = 0.0
    capital_seconds_locked = 0.0

    for mid, info in mapping.items():
        yes_arr = book.get(info["yes"])
        no_arr  = book.get(info["no"])
        if not yes_arr or not no_arr:
            continue

        # Find an entry point — first sync'd YES+NO that qualifies.
        no_times = [x["received_at_ns"] for x in no_arr]
        entry_idx = None
        for i, y in enumerate(yes_arr):
            if y["best_ask"] is None: continue
            j = bisect.bisect_right(no_times, y["received_at_ns"]) - 1
            if j < 0: continue
            n = no_arr[j]
            if n["best_ask"] is None: continue
            if abs(y["received_at_ns"] - n["received_at_ns"]) > 2_000_000_000: continue

            ya, na = y["best_ask"], n["best_ask"]
            if not (SCALP_MIN_PRICE <= ya <= SCALP_MAX_PRICE): continue
            if not (SCALP_MIN_PRICE <= na <= SCALP_MAX_PRICE): continue
            if abs(ya - na) > SCALP_MAX_SKEW: continue
            if ya + na > SCALP_MAX_SUM: continue
            entry_idx = (i, j)
            entry_ns = y["received_at_ns"]
            entry_y_ask, entry_n_ask = ya, na
            break
        if entry_idx is None:
            continue

        # Walk forward, tracking the two legs independently. Each leg can:
        #   - scratch at +2c above entry (lock small profit)
        #   - stop-loss at -25c below entry (cut losers)
        #   - hit ride target 0.90 (full take-profit)
        #   - fall through to final book bid (proxy for settle)
        yes_shares = SCALP_STAKE_USD / entry_y_ask
        no_shares  = SCALP_STAKE_USD / entry_n_ask
        cost = SCALP_STAKE_USD * 2

        match_end_ns = max(yes_arr[-1]["received_at_ns"], no_arr[-1]["received_at_ns"])
        i_y = entry_idx[0]
        i_n = entry_idx[1]

        def _walk_leg(arr, start_idx, entry_ask, shares):
            """Walk the leg's book ticks and return (proceeds_usd, exit_reason)."""
            scratch_px = entry_ask + SCALP_SCRATCH_CENTS
            stop_px    = entry_ask - SCALP_STOP_LOSS_CENTS
            for tick in arr[start_idx + 1:]:
                bid = tick.get("best_bid")
                if bid is None: continue
                if bid >= SCALP_RIDE_TARGET:
                    return shares * bid, "ride_target"
                if bid >= scratch_px:
                    return shares * bid, "scratched"
                if bid <= stop_px:
                    return shares * bid, "stopped"
            # No trigger hit — settle proxy via last visible bid (assume the
            # winning side's price approaches 1.0 and the losing side's 0.0,
            # but we don't actually know which won; the last bid is our best
            # estimate of settle value).
            last_bid = next((t["best_bid"] for t in reversed(arr) if t["best_bid"] is not None), 0.0)
            return shares * last_bid, "settle_proxy"

        yes_proceeds, yes_exit = _walk_leg(yes_arr, i_y, entry_y_ask, yes_shares)
        no_proceeds,  no_exit  = _walk_leg(no_arr,  i_n, entry_n_ask, no_shares)

        total_proceeds = yes_proceeds + no_proceeds
        pnl = total_proceeds - cost
        hold_sec = max((match_end_ns - entry_ns) / 1e9, 1.0)
        capital_locked_usd_total += cost
        capital_seconds_locked += cost * hold_sec
        trades.append({
            "strategy": "scalp", "match_id": mid, "ns": entry_ns,
            "pnl": pnl, "size_usd": cost, "hold_sec": hold_sec,
            "entry_yes_ask": entry_y_ask, "entry_no_ask": entry_n_ask,
            "yes_exit": yes_exit, "no_exit": no_exit,
        })

    return trades, capital_locked_usd_total, capital_seconds_locked


# ============================================================================
def summarize(trades, label):
    if not trades:
        return f"{label}: no trades"
    pnls = sorted(t["pnl"] for t in trades)
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    total = sum(pnls)
    total_size = sum(t.get("size_usd", 5.0) for t in trades)
    return (f"{label}\n"
            f"  trades:        {n:,}\n"
            f"  total PnL:     ${total:+.2f}\n"
            f"  $/trade:       ${total/n:+.4f}\n"
            f"  win rate:      {100*wins/n:.1f}%\n"
            f"  median:        ${pnls[n//2]:+.4f}\n"
            f"  p25 / p75:     ${pnls[n//4]:+.4f} / ${pnls[3*n//4]:+.4f}\n"
            f"  total deployed: ${total_size:.2f}\n"
            f"  ROI:           {100*total/max(total_size, 1):.2f}%")


def main():
    t0 = time.time()
    mapping = load_mapping()
    snaps = load_snapshots()
    print(f"Loaded {len(snaps)} matches in snapshots ({time.time()-t0:.2f}s)")

    joinable = [mid for mid in snaps if mid in mapping]
    yes_tokens = {mapping[mid]["yes"] for mid in joinable}
    no_tokens  = {mapping[mid]["no"]  for mid in joinable}
    all_tokens = yes_tokens | no_tokens
    t0 = time.time()
    book = load_book_full(all_tokens)
    print(f"Loaded book for {len(book)} assets ({time.time()-t0:.2f}s)")

    print("\n" + "="*72)
    print("STRATEGY 1: CONTINUOUS")
    print("="*72)
    t0 = time.time()
    cont_trades = backtest_continuous(snaps, book, mapping)
    print(f"  (built {len(cont_trades)} trades in {time.time()-t0:.2f}s)")
    print(summarize(cont_trades, "Continuous result"))

    print("\n" + "="*72)
    print("STRATEGY 2: ARBITRAGE")
    print("="*72)
    t0 = time.time()
    arb_trades, arb_capital, arb_capital_sec = backtest_arb(book, mapping)
    print(f"  (scanned in {time.time()-t0:.2f}s)")
    print(summarize(arb_trades, "Arb result"))
    if arb_trades:
        avg_hold = sum(t["hold_sec"] for t in arb_trades) / len(arb_trades)
        avg_capital_locked = arb_capital_sec / (13 * 86400)  # avg over the 13-day window
        print(f"  avg hold:      {avg_hold:.0f}s ({avg_hold/60:.1f}min)")
        print(f"  avg capital locked (over 13 days): ${avg_capital_locked:.2f}")

    print("\n" + "="*72)
    print("STRATEGY 3: SCALP")
    print("="*72)
    t0 = time.time()
    scalp_trades, scalp_capital, scalp_capital_sec = backtest_scalp(book, mapping)
    print(f"  (scanned in {time.time()-t0:.2f}s)")
    print(summarize(scalp_trades, "Scalp result"))
    if scalp_trades:
        avg_hold = sum(t["hold_sec"] for t in scalp_trades) / len(scalp_trades)
        avg_capital_locked = scalp_capital_sec / (13 * 86400)
        print(f"  avg hold:      {avg_hold/60:.1f}min")
        print(f"  avg capital locked (over 13 days): ${avg_capital_locked:.2f}")
        from collections import Counter
        exits = Counter()
        for t in scalp_trades:
            exits[t["yes_exit"]] += 1
            exits[t["no_exit"]]  += 1
        print(f"  exit reasons (per leg, n={2*len(scalp_trades)}):")
        for r, n in exits.most_common():
            print(f"    {r:<16} {n}")

    print("\n" + "="*72)
    print("COMBINED (independent execution, 13-day window)")
    print("="*72)
    total = sum(t["pnl"] for t in cont_trades + arb_trades + scalp_trades)
    n_total = len(cont_trades) + len(arb_trades) + len(scalp_trades)
    print(f"  total trades: {n_total:,}")
    print(f"  total PnL:    ${total:+.2f}")
    print(f"  per day:      ${total/13:+.2f}")
    print(f"  per strategy:")
    print(f"    continuous: ${sum(t['pnl'] for t in cont_trades):+.2f}  ({len(cont_trades)} trades)")
    print(f"    arb:        ${sum(t['pnl'] for t in arb_trades):+.2f}  ({len(arb_trades)} trades)")
    print(f"    scalp:      ${sum(t['pnl'] for t in scalp_trades):+.2f}  ({len(scalp_trades)} trades)")


if __name__ == "__main__":
    main()
