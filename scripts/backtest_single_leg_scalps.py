#!/usr/bin/env python3
"""Mine single-leg scalp exits from snapshot/book entry signals.

Research only. This uses executable prices:
- entry = available best ask at the signal token
- exit = available best bid after the entry

It compares small TP/SL/time exits against hold-to-settlement for the same
entry families.
"""
from __future__ import annotations

import bisect
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as pds

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import scripts.backtest_value_engine as value_bt
import scripts.mine_new_settlement_strategy as miner

STAKE = 20.0
ENTRY_MAX_BOOK_AGE_MS = 15_000

TP_CENTS = [0.02, 0.03, 0.05, 0.08, 0.10, 0.15]
SL_CENTS = [0.03, 0.05, 0.08, 0.10, 0.15, 0.20]
HORIZONS_SEC = [60, 120, 300, 600, 1200, 2400]


def _side_is_yes(mapping: dict[str, Any], side: str) -> bool:
    normal = mapping.get("steam_side_mapping", "normal") == "normal"
    return (side == "radiant" and normal) or (side == "dire" and not normal)


def _token(mapping: dict[str, Any], side: str) -> str:
    return str(mapping["yes_token_id"] if _side_is_yes(mapping, side) else mapping["no_token_id"])


def _settlement_pnl(row: dict[str, Any]) -> float:
    return ((1.0 if row["won"] else 0.0) - float(row["ask"])) / float(row["ask"]) * STAKE


def _attach_entry_ns(rows: list[dict[str, Any]], snapshots: dict[str, list[dict]]) -> None:
    by_gt: dict[str, dict[int, int]] = {}
    for match_id, snaps in snapshots.items():
        by_gt[match_id] = {
            int(row["game_time_sec"]): int(row["received_at_ns"])
            for row in snaps
            if row.get("game_time_sec") is not None and row.get("received_at_ns") is not None
        }
    for row in rows:
        row["received_at_ns"] = by_gt.get(row["match_id"], {}).get(int(row["gt"]), 0)


def load_books_full(tokens: set[str]) -> dict[str, tuple[list[int], list[dict]]]:
    dataset = pds.dataset(REPO_ROOT / "data_v2" / "book_ticks", format="parquet", partitioning="hive")
    table = dataset.to_table(
        columns=["asset_id", "received_at_ns", "best_bid", "best_ask", "bid_size", "ask_size", "mid", "date"],
        filter=pc.is_in(pc.field("asset_id"), pa.array(list(tokens))),
    )
    by_asset: dict[str, list[dict]] = defaultdict(list)
    for row in table.to_pylist():
        if row.get("received_at_ns") is not None:
            by_asset[str(row["asset_id"])].append(row)

    out: dict[str, tuple[list[int], list[dict]]] = {}
    for asset_id, rows in by_asset.items():
        rows.sort(key=lambda row: int(row["received_at_ns"]))
        out[asset_id] = ([int(row["received_at_ns"]) for row in rows], rows)
    return out


def _entry_book(book: dict[str, tuple[list[int], list[dict]]], token: str, ns: int) -> tuple[dict | None, float | None]:
    item = book.get(str(token))
    if not item or not ns:
        return None, None
    times, rows = item
    idx = bisect.bisect_right(times, ns) - 1
    if idx < 0:
        return None, None
    row = rows[idx]
    age_ms = (ns - int(row["received_at_ns"])) / 1_000_000
    return row, age_ms


def _scalp_exit(
    *,
    book: dict[str, tuple[list[int], list[dict]]],
    token: str,
    entry_ns: int,
    entry_ask: float,
    tp_cents: float,
    sl_cents: float,
    horizon_sec: int,
) -> dict[str, Any] | None:
    item = book.get(str(token))
    if not item or not entry_ns:
        return None
    times, rows = item
    idx = bisect.bisect_right(times, entry_ns)
    if idx >= len(rows):
        return None

    tp = entry_ask + tp_cents
    sl = max(0.01, entry_ask - sl_cents)
    horizon_ns = entry_ns + horizon_sec * 1_000_000_000
    last_bid: float | None = None
    last_ns = entry_ns

    for row in rows[idx:]:
        ns = int(row["received_at_ns"])
        if ns > horizon_ns:
            break
        bid = row.get("best_bid")
        if bid is None:
            continue
        bid = float(bid)
        last_bid = bid
        last_ns = ns
        if bid >= tp:
            return {"exit_bid": bid, "exit_ns": ns, "reason": "tp", "seconds": (ns - entry_ns) / 1e9}
        if bid <= sl:
            return {"exit_bid": bid, "exit_ns": ns, "reason": "sl", "seconds": (ns - entry_ns) / 1e9}

    if last_bid is None:
        return None
    return {"exit_bid": last_bid, "exit_ns": last_ns, "reason": "horizon", "seconds": (last_ns - entry_ns) / 1e9}


def _pnl_from_exit(entry_ask: float, exit_bid: float) -> float:
    return (exit_bid - entry_ask) / entry_ask * STAKE


def _first_per_match(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in sorted(rows, key=lambda x: (str(x["date"]), str(x["match_id"]), int(x["gt"]), str(x["side_name"]))):
        if row["match_id"] in seen:
            continue
        seen.add(row["match_id"])
        out.append(row)
    return out


def _summarize_settlement(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "n=0"
    pnls = [_settlement_pnl(row) for row in rows]
    wins = sum(int(row["won"]) for row in rows)
    return (
        f"n={len(rows)} wins={wins}/{len(rows)} win={wins/len(rows)*100:.1f}% "
        f"pnl=${sum(pnls):+.2f} roi={sum(pnls)/(STAKE*len(rows))*100:+.1f}% "
        f"avg_ask={sum(float(row['ask']) for row in rows)/len(rows):.3f}"
    )


def _run_grid(
    *,
    rows: list[dict[str, Any]],
    markets: dict[str, dict],
    book: dict[str, tuple[list[int], list[dict]]],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for tp in TP_CENTS:
        for sl in SL_CENTS:
            for horizon in HORIZONS_SEC:
                pnls: list[float] = []
                reasons: Counter = Counter()
                skipped = 0
                total_seconds = 0.0
                for row in rows:
                    mapping = markets[row["match_id"]]
                    token = _token(mapping, row["side"])
                    entry_ns = int(row.get("received_at_ns") or 0)
                    entry_ask = float(row["ask"])
                    entry_row, age_ms = _entry_book(book, token, entry_ns)
                    if not entry_row or age_ms is None or age_ms > ENTRY_MAX_BOOK_AGE_MS:
                        skipped += 1
                        continue
                    live_ask = entry_row.get("best_ask")
                    if live_ask is None:
                        skipped += 1
                        continue
                    entry_ask = float(live_ask)
                    exit_row = _scalp_exit(
                        book=book,
                        token=token,
                        entry_ns=entry_ns,
                        entry_ask=entry_ask,
                        tp_cents=tp,
                        sl_cents=sl,
                        horizon_sec=horizon,
                    )
                    if not exit_row:
                        skipped += 1
                        continue
                    pnl = _pnl_from_exit(entry_ask, float(exit_row["exit_bid"]))
                    pnls.append(pnl)
                    reasons[str(exit_row["reason"])] += 1
                    total_seconds += float(exit_row["seconds"])
                if not pnls:
                    continue
                results.append(
                    {
                        "tp": tp,
                        "sl": sl,
                        "horizon": horizon,
                        "n": len(pnls),
                        "skipped": skipped,
                        "wins": sum(1 for pnl in pnls if pnl > 0),
                        "pnl": sum(pnls),
                        "roi": sum(pnls) / (STAKE * len(pnls)) * 100,
                        "avg": sum(pnls) / len(pnls),
                        "median": sorted(pnls)[len(pnls) // 2],
                        "reasons": dict(reasons),
                        "avg_seconds": total_seconds / len(pnls),
                    }
                )
    results.sort(key=lambda row: (row["roi"], row["n"]), reverse=True)
    return results


def _print_top(label: str, rows: list[dict[str, Any]], markets: dict[str, dict], book: dict[str, tuple[list[int], list[dict]]]) -> None:
    print(f"\n=== {label} ===")
    print("settlement", _summarize_settlement(rows))
    results = _run_grid(rows=rows, markets=markets, book=book)
    if not results:
        print("scalp no executable exits")
        return
    print("top scalp exits")
    for r in results[:10]:
        print(
            f"  tp=+{r['tp']:.2f} sl=-{r['sl']:.2f} hold<={r['horizon']:4d}s "
            f"n={r['n']:3d} skip={r['skipped']:2d} win={r['wins']/r['n']*100:5.1f}% "
            f"pnl=${r['pnl']:+7.2f} roi={r['roi']:+6.1f}% avg=${r['avg']:+.3f} "
            f"med=${r['median']:+.3f} avg_hold={r['avg_seconds']:5.0f}s exits={r['reasons']}"
        )
    print("worst scalp exits")
    for r in sorted(results, key=lambda row: row["roi"])[:3]:
        print(
            f"  tp=+{r['tp']:.2f} sl=-{r['sl']:.2f} hold<={r['horizon']:4d}s "
            f"n={r['n']:3d} pnl=${r['pnl']:+7.2f} roi={r['roi']:+6.1f}% exits={r['reasons']}"
        )


def main() -> None:
    events = miner.build_events()
    markets, skipped = value_bt.load_markets()
    snapshots = value_bt.load_snapshots(set(markets))
    _attach_entry_ns(events, snapshots)
    tokens = {str(m["yes_token_id"]) for m in markets.values()} | {str(m["no_token_id"]) for m in markets.values()}
    book = load_books_full(tokens)

    print(f"markets={len(markets)} skipped={dict(skipped)} events={len(events)} book_assets={len(book)}")

    strategies: list[tuple[str, Callable[[dict[str, Any]], bool]]] = [
        (
            "early fav A .50-.75 5-15m",
            lambda r: r["side_name"] == "fav" and 0.50 <= r["ask"] <= 0.75 and 300 <= r["gt"] <= 900,
        ),
        (
            "early fav B .65-.84 5-15m + nw/model",
            lambda r: (
                r["side_name"] == "fav"
                and 0.65 <= r["ask"] <= 0.84
                and 300 <= r["gt"] <= 900
                and r["same_as_nw"]
                and r["same_as_model"]
            ),
        ),
        (
            "value-like nw edge>=.10 fair>=.70 10-40m",
            lambda r: (
                r["side_name"] == "nw"
                and r["edge"] >= 0.10
                and r["fair"] >= 0.70
                and 0.45 <= r["ask"] <= 0.84
                and 600 <= r["gt"] <= 2400
            ),
        ),
        (
            "value-like nw edge>=.15 fair>=.70 10-40m",
            lambda r: (
                r["side_name"] == "nw"
                and r["edge"] >= 0.15
                and r["fair"] >= 0.70
                and 0.45 <= r["ask"] <= 0.84
                and 600 <= r["gt"] <= 2400
            ),
        ),
        (
            "book fav + model disagree contrarian .35-.65 5-20m",
            lambda r: (
                r["side_name"] == "fav"
                and 0.35 <= r["ask"] <= 0.65
                and 300 <= r["gt"] <= 1200
                and r["model_side"] is not None
                and not r["same_as_model"]
            ),
        ),
        (
            "fresh kill swing with nw confirmation 5-25m",
            lambda r: (
                r["side_name"] == "score"
                and r["same_as_nw"]
                and r["score_delta"] is not None
                and abs(int(r["score_delta"])) >= 2
                and 0.35 <= r["ask"] <= 0.85
                and 300 <= r["gt"] <= 1500
            ),
        ),
        (
            "nw momentum side_slope>=2k 5-25m",
            lambda r: (
                r["side_name"] == "nw"
                and r["side_slope"] >= 2000
                and 0.35 <= r["ask"] <= 0.85
                and 300 <= r["gt"] <= 1500
            ),
        ),
    ]

    for label, predicate in strategies:
        rows = _first_per_match([row for row in events if predicate(row) and row.get("received_at_ns")])
        _print_top(label, rows, markets, book)


if __name__ == "__main__":
    main()
