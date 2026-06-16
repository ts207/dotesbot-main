"""Backtest the 'buy YES + NO, scratch at break-even, ride winner' strategy.

For each captured BLAST Slam / DreamLeague match, simulate:
  1. Enter both YES and NO at their early-game asks (~minute 1 of game)
  2. Place limit-sell orders at entry+0.02 on each side (scratch)
  3. Whichever side hits 0.52 bid first → close it (recover ~$0.52)
  4. Remaining side: track to peak bid, then either sell at peak or settle
  5. Compute PnL per match using fees + spread

Strategy variants compared:
  - "always_scratch_then_ride_settle": scratch one, hold other to game-over
  - "always_scratch_then_take_at_peak": scratch one, sell other at its peak

Outputs an aggregate EV per match + per scenario breakdown.
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ENTRY_TARGET_PRICE = 0.52   # scratch limit
FEE_RATE = 0.02              # 2% per fill (Polymarket-ish, generous)
MIN_GAME_TIME_FOR_ENTRY = 30  # don't enter before gt=30s


def _load_markets():
    data = yaml.safe_load(open(ROOT / "markets.yaml")) or {}
    by_match = {}
    for m in data.get("markets", []):
        mid = str(m.get("dota_match_id") or "")
        if not mid or mid.startswith("STEAM_MATCH"):
            continue
        if str(m.get("confidence") or 0) != "1.0" and float(m.get("confidence", 0)) != 1.0:
            continue
        # Prefer MAP_WINNER over MATCH_WINNER for cleaner per-game scoring.
        if mid in by_match and by_match[mid].get("market_type") == "MAP_WINNER":
            continue
        by_match[mid] = m
    return by_match


def _load_book(asset_id: str, ts_start: int, ts_end: int) -> list[dict]:
    """Stream book_events.csv, keep only rows for this asset within window."""
    out = []
    with (ROOT / "logs" / "book_events.csv").open() as f:
        for row in csv.DictReader(f):
            if row.get("asset_id") != asset_id:
                continue
            try:
                ts_ms = _parse_ts_ms(row["timestamp_utc"])
            except Exception:
                continue
            if ts_ms < ts_start or ts_ms > ts_end:
                continue
            out.append({"ts_ms": ts_ms,
                        "bid": _f(row.get("best_bid")),
                        "ask": _f(row.get("best_ask"))})
    return out


def _parse_ts_ms(s: str) -> int:
    from datetime import datetime
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


def _f(v) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _load_match_windows() -> dict[str, tuple[int, int, int]]:
    """For each match_id in raw_snapshots (current + backups), return
    (first_ts_ms, last_ts_ms, final_rad_lead).

    Match considered "finished" if either game_over=True OR the final snapshot
    shows a clear winner (|rad_lead| >= 10000 AND game_time >= 20 min) — game
    was clearly decided even if the snapshot logger stopped before game_over.
    """
    import glob
    snaps = defaultdict(list)
    files = [str(ROOT / "logs" / "raw_snapshots.csv")] + \
            sorted(glob.glob(str(ROOT / "logs" / "raw_snapshots.csv.*.bak")))
    for path in files:
        try:
            f = open(path)
        except OSError:
            continue
        with f:
            for row in csv.DictReader(f):
                mid = (row.get("match_id") or "").strip()
                if not mid:
                    continue
                try:
                    ts_ms = _parse_ts_ms(row["received_at_utc"])
                except Exception:
                    continue
                snaps[mid].append({
                    "ts_ms": ts_ms,
                    "gt": int(float(row.get("game_time_sec") or 0)),
                    "rad_lead": int(float(row.get("radiant_lead") or 0)),
                    "go": str(row.get("game_over", "")).lower() in ("true", "1", "yes"),
                })
    out = {}
    for mid, rows in snaps.items():
        # dedupe by ts_ms (a match may appear in current + bak)
        seen = {}
        for r in rows:
            seen[r["ts_ms"]] = r
        rows = sorted(seen.values(), key=lambda r: r["ts_ms"])
        post_start = [r for r in rows if r["gt"] >= MIN_GAME_TIME_FOR_ENTRY]
        if not post_start:
            continue
        last = rows[-1]
        if last["go"]:
            finished = True
        elif abs(last["rad_lead"]) >= 10000 and last["gt"] >= 1200:
            finished = True  # inferred winner: clear lead, mid-game or later
        else:
            finished = False
        if not finished:
            continue
        out[mid] = (rows[0]["ts_ms"], last["ts_ms"], last["rad_lead"])
    return out


def simulate_one(mid: str, market: dict, t0: int, tN: int, final_rad_lead: int) -> dict | None:
    yes_tok = str(market.get("yes_token_id") or "")
    no_tok = str(market.get("no_token_id") or "")
    if not yes_tok or not no_tok:
        return None
    yes_team = (market.get("yes_team") or "").lower()
    steam_radiant_team = (market.get("steam_radiant_team") or "").lower()
    # yes side wins if yes_team == radiant team and radiant won, or vice versa.
    radiant_won = final_rad_lead > 0
    yes_is_radiant = yes_team and steam_radiant_team and yes_team == steam_radiant_team
    yes_wins = (yes_is_radiant and radiant_won) or (not yes_is_radiant and not radiant_won)

    yes_book = _load_book(yes_tok, t0, tN)
    no_book = _load_book(no_tok, t0, tN)
    if not yes_book or not no_book:
        return None

    # Entry: take first available ask on each after t0
    def first_ask_after(book, t):
        for r in book:
            if r["ts_ms"] >= t and r["ask"] is not None and 0 < r["ask"] < 1:
                return r["ask"], r["ts_ms"]
        return None, None

    yes_entry_px, yes_entry_t = first_ask_after(yes_book, t0)
    no_entry_px, no_entry_t = first_ask_after(no_book, t0)
    if yes_entry_px is None or no_entry_px is None:
        return None

    # Skip if entry was after a clear leader emerged (entry > 0.65 = late)
    if yes_entry_px > 0.65 or no_entry_px > 0.65:
        return None

    cost = (yes_entry_px + no_entry_px) * (1 + FEE_RATE)

    # Scratch: find first bid >= ENTRY_TARGET on each side after entry
    def first_bid_ge(book, threshold, after_t):
        for r in book:
            if r["ts_ms"] >= after_t and r["bid"] is not None and r["bid"] >= threshold:
                return r["bid"], r["ts_ms"]
        return None, None

    yes_scratch_px, yes_scratch_t = first_bid_ge(yes_book, ENTRY_TARGET_PRICE, max(yes_entry_t, no_entry_t))
    no_scratch_px, no_scratch_t = first_bid_ge(no_book, ENTRY_TARGET_PRICE, max(yes_entry_t, no_entry_t))

    # Peak bids (for "sell at peak" variant)
    yes_peak = max((r["bid"] for r in yes_book if r["bid"] is not None), default=0.0)
    no_peak = max((r["bid"] for r in no_book if r["bid"] is not None), default=0.0)

    # Settlement
    yes_settle = 1.0 if yes_wins else 0.0
    no_settle = 0.0 if yes_wins else 1.0

    # Strategy A: scratch + hold loser to settle
    if yes_scratch_px is not None and no_scratch_px is not None:
        # both scratched — best case
        revenue_A = (yes_scratch_px + no_scratch_px) * (1 - FEE_RATE)
    elif yes_scratch_px is not None:
        revenue_A = yes_scratch_px * (1 - FEE_RATE) + no_settle  # no settlement, no fee
    elif no_scratch_px is not None:
        revenue_A = no_scratch_px * (1 - FEE_RATE) + yes_settle
    else:
        revenue_A = (yes_settle + no_settle)  # neither scratched, hold both to settle
    pnl_A = revenue_A - cost

    # Strategy B: scratch one + ride other to its peak
    # Sells the FIRST scratched side at 0.52, holds other to its peak (capped at 0.95
    # to be conservative — assume can't always sell exactly at top tick)
    yes_first = (yes_scratch_t or 1e20) <= (no_scratch_t or 1e20)
    if yes_scratch_px is None and no_scratch_px is None:
        revenue_B = yes_settle + no_settle
    elif yes_first and yes_scratch_px is not None:
        # sold YES at 0.52, then ride NO
        # find NO's peak AFTER yes_scratch_t
        no_peak_after = max((r["bid"] for r in no_book if r["bid"] is not None and r["ts_ms"] > yes_scratch_t), default=0.0)
        ride_no = min(no_peak_after, 0.95) if no_peak_after >= ENTRY_TARGET_PRICE else no_settle
        revenue_B = (yes_scratch_px + ride_no) * (1 - FEE_RATE)
    elif no_scratch_px is not None:
        yes_peak_after = max((r["bid"] for r in yes_book if r["bid"] is not None and r["ts_ms"] > no_scratch_t), default=0.0)
        ride_yes = min(yes_peak_after, 0.95) if yes_peak_after >= ENTRY_TARGET_PRICE else yes_settle
        revenue_B = (no_scratch_px + ride_yes) * (1 - FEE_RATE)
    else:
        revenue_B = revenue_A
    pnl_B = revenue_B - cost

    return {
        "match_id": mid,
        "name": (market.get("name") or "")[:50],
        "yes_entry": yes_entry_px,
        "no_entry": no_entry_px,
        "yes_scratched_at": yes_scratch_px,
        "no_scratched_at": no_scratch_px,
        "yes_peak": yes_peak,
        "no_peak": no_peak,
        "yes_wins": yes_wins,
        "cost": cost,
        "pnl_settle_hold": pnl_A,
        "pnl_scratch_and_ride_peak": pnl_B,
    }


def main():
    markets = _load_markets()
    windows = _load_match_windows()
    print(f"Loaded {len(markets)} mapped markets, {len(windows)} finished matches.\n")

    results = []
    for mid, (t0, tN, rl) in windows.items():
        if mid not in markets:
            continue
        r = simulate_one(mid, markets[mid], t0, tN, rl)
        if r:
            results.append(r)
    if not results:
        print("No simulatable matches found.")
        return

    print(f"Simulated {len(results)} matches.\n")
    n = len(results)

    def stats(key, label):
        vals = [r[key] for r in results]
        avg = sum(vals) / n
        wins = sum(1 for v in vals if v > 0)
        median = sorted(vals)[n//2]
        worst = min(vals); best = max(vals)
        print(f"{label}: n={n}  avg=${avg:+.3f}  median=${median:+.3f}  win%={wins/n*100:.0f}%  worst=${worst:+.3f}  best=${best:+.3f}")

    stats("pnl_settle_hold", "A) Scratch one, hold loser to settle ")
    stats("pnl_scratch_and_ride_peak", "B) Scratch one, ride other to peak ")

    print("\nPer-match detail (sorted by strategy B PnL):")
    print(f"{'match':12} {'name':30} {'YESe':>5} {'NOe':>5} {'YESs':>5} {'NOs':>5} {'YESp':>5} {'NOp':>5} {'win':>3} {'A':>7} {'B':>7}")
    for r in sorted(results, key=lambda x: -x["pnl_scratch_and_ride_peak"]):
        print(f"{r['match_id']:12} {r['name'][:30]:30} "
              f"{r['yes_entry']:>5.2f} {r['no_entry']:>5.2f} "
              f"{(r['yes_scratched_at'] or 0):>5.2f} {(r['no_scratched_at'] or 0):>5.2f} "
              f"{r['yes_peak']:>5.2f} {r['no_peak']:>5.2f} "
              f"{'Y' if r['yes_wins'] else 'N':>3} "
              f"{r['pnl_settle_hold']:>+7.3f} {r['pnl_scratch_and_ride_peak']:>+7.3f}")


if __name__ == "__main__":
    main()
