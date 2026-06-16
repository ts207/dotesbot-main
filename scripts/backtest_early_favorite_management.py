#!/usr/bin/env python3
"""Backtest early-favorite settlement entries and simple management exits.

This is research-only. It compares hold-to-settlement against fixed book exits
for the shadow strategies implemented in early_favorite_shadow.py.
"""
from __future__ import annotations

import bisect
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import scripts.mine_new_settlement_strategy as miner
import scripts.backtest_value_engine as value_bt

STAKE = 20.0
FRESH_MS = 15_000
HORIZONS = [30, 60, 120, 300, 600, 1200]


def _side_is_yes(mapping: dict[str, Any], side: str) -> bool:
    normal = mapping.get("steam_side_mapping", "normal") == "normal"
    return (side == "radiant" and normal) or (side == "dire" and not normal)


def _token(mapping: dict[str, Any], side: str) -> str:
    return str(mapping["yes_token_id"] if _side_is_yes(mapping, side) else mapping["no_token_id"])


def _book_mid_age(book: dict[str, tuple[list[int], list[dict]]], token_id: str, ns: int) -> tuple[float | None, float | None]:
    item = book.get(str(token_id))
    if not item:
        return None, None
    times, rows = item
    idx = bisect.bisect_right(times, ns) - 1
    if idx < 0:
        return None, None
    mid = rows[idx].get("mid")
    if mid is None:
        return None, None
    return float(mid), (ns - int(rows[idx]["received_at_ns"])) / 1_000_000


def _settlement_pnl(row: dict[str, Any]) -> float:
    return ((1.0 if row["won"] else 0.0) - row["ask"]) / row["ask"] * STAKE


def _group(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        out[str(row.get(key) or "")].append(row)
    return out


def _summarize(rows: list[dict[str, Any]]) -> str:
    n = len(rows)
    wins = sum(int(row["won"]) for row in rows)
    pnl = sum(_settlement_pnl(row) for row in rows)
    roi = pnl / (STAKE * n) * 100 if n else 0.0
    avg_ask = sum(row["ask"] for row in rows) / n if n else 0.0
    return f"n={n} wins={wins}/{n} win={(wins/n*100 if n else 0):.1f}% pnl=${pnl:+.2f} roi={roi:+.1f}% avg_ask={avg_ask:.3f}"


def _fixed_exit_pnl(
    row: dict[str, Any],
    *,
    mapping: dict[str, Any],
    book: dict[str, tuple[list[int], list[dict]]],
    horizon_sec: int,
) -> float | None:
    ns = int(row.get("received_at_ns") or 0)
    if not ns:
        return None
    token_id = _token(mapping, row["side"])
    mid, age = _book_mid_age(book, token_id, ns + horizon_sec * 1_000_000_000)
    if mid is None or age is None or age > FRESH_MS:
        return None
    # Conservative bid proxy. The data_v2 book loader has mid/ask; using mid-1c
    # avoids overstating fixed-exit performance.
    exit_bid = max(0.01, mid - 0.01)
    return (exit_bid - row["ask"]) / row["ask"] * STAKE


def _attach_entry_ns(rows: list[dict[str, Any]], snapshots: dict[str, list[dict]]) -> None:
    for row in rows:
        if row.get("received_at_ns"):
            continue
        for snap in snapshots.get(row["match_id"], []):
            if int(snap.get("game_time_sec") or -999) == int(row["gt"]):
                row["received_at_ns"] = int(snap.get("received_at_ns") or 0)
                break


def _report(
    label: str,
    rows: list[dict[str, Any]],
    *,
    markets: dict[str, dict],
    book: dict[str, tuple[list[int], list[dict]]],
) -> None:
    print(f"\n{label}")
    print(" settlement", _summarize(rows))
    print(" by_source", {k: (len(v), round(sum(_settlement_pnl(r) for r in v) / (STAKE * len(v)) * 100, 1)) for k, v in _group(rows, "source").items()})
    print(" by_date", {k: (len(v), round(sum(_settlement_pnl(r) for r in v) / (STAKE * len(v)) * 100, 1)) for k, v in sorted(_group(rows, "date").items())})
    print(" exit_if_fresh_else_hold")
    for horizon in HORIZONS:
        values: list[float] = []
        fresh = 0
        for row in rows:
            exit_pnl = _fixed_exit_pnl(row, mapping=markets[row["match_id"]], book=book, horizon_sec=horizon)
            if exit_pnl is None:
                values.append(_settlement_pnl(row))
            else:
                fresh += 1
                values.append(exit_pnl)
        roi = sum(values) / (STAKE * len(rows)) * 100 if rows else 0.0
        print(f"  +{horizon:4d}s fresh={fresh:2d}/{len(rows):<2d} pnl=${sum(values):+8.2f} roi={roi:+6.1f}%")


def main() -> None:
    events = miner.build_events()
    markets, _ = value_bt.load_markets()
    snapshots = value_bt.load_snapshots(set(markets))
    tokens = {str(m["yes_token_id"]) for m in markets.values()} | {str(m["no_token_id"]) for m in markets.values()}
    book = value_bt.load_books(tokens)
    _attach_entry_ns(events, snapshots)

    strategies: list[tuple[str, Callable[[dict[str, Any]], bool]]] = [
        (
            "A fav .50-.75 5-15m",
            lambda r: r["side_name"] == "fav" and 0.50 <= r["ask"] <= 0.75 and 300 <= r["gt"] <= 900,
        ),
        (
            "B fav .65-.84 5-15m + networth/model",
            lambda r: (
                r["side_name"] == "fav"
                and 0.65 <= r["ask"] <= 0.84
                and 300 <= r["gt"] <= 900
                and r["same_as_nw"]
                and r["same_as_model"]
            ),
        ),
        (
            "strict VALUE-like edge>=.15 fair>=.70",
            lambda r: (
                r["side_name"] == "nw"
                and r["edge"] >= 0.15
                and r["fair"] >= 0.70
                and 0.55 <= r["ask"] <= 0.84
                and 600 <= r["gt"] <= 2400
            ),
        ),
    ]

    for label, predicate in strategies:
        rows = miner.first_per_match([row for row in events if predicate(row)])
        _report(label, rows, markets=markets, book=book)


if __name__ == "__main__":
    main()
