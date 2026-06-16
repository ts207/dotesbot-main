#!/usr/bin/env python3
"""Analyze ways to use snapshots for more settlement winners.

This script compares:
- buying the book favorite,
- buying the snapshot model favorite,
- buying only when model and book agree,
- buying when the model disagrees with book.

It uses cross-fitted model probabilities from backtest_snapshot_winner_model.py,
so each date is scored by a model trained on other dates.
"""
from __future__ import annotations

import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import scripts.backtest_snapshot_winner_model as snap_model
import scripts.backtest_value_engine as value_bt


def _load_scored_rows() -> list[dict[str, Any]]:
    outcomes, outcome_sources = value_bt.load_outcomes()
    markets, _skipped = value_bt.load_markets()
    snapshots = value_bt.load_snapshots(set(markets))
    tokens = {str(markets[mid]["yes_token_id"]) for mid in snapshots} | {
        str(markets[mid]["no_token_id"]) for mid in snapshots
    }
    book = value_bt.load_books(tokens)
    joined = {
        match_id: rows
        for match_id, rows in snapshots.items()
        if str(markets[match_id]["yes_token_id"]) in book
        and str(markets[match_id]["no_token_id"]) in book
    }
    rows, _coverage, _unresolved = snap_model.build_rows(
        snapshots=joined,
        markets=markets,
        book=book,
        outcomes=outcomes,
        outcome_sources=outcome_sources,
    )
    return snap_model.crossfit(rows)


def _candidate_rows(rows: list[dict[str, Any]], *, allow_final_book: bool = True) -> list[dict[str, Any]]:
    out = []
    seen: set[str] = set()
    for row in rows:
        if not allow_final_book and row.get("outcome_source") == "final_book_mid":
            continue
        if row["match_id"] in seen:
            continue
        if row["game_time"] < 600 or row["game_time"] > 2400:
            continue
        if max(row["yes_age_ms"], row["no_age_ms"]) > 15000:
            continue
        seen.add(row["match_id"])
        book_side = "YES" if row["yes_ask"] >= row["no_ask"] else "NO"
        model_side = "YES" if row["p_yes"] >= 0.5 else "NO"
        out.append({**row, "book_side": book_side, "model_side": model_side})
    return out


def _pnl(side: str, row: dict[str, Any], stake: float = 20.0) -> tuple[int, float, float]:
    if side == "YES":
        ask = row["yes_ask"]
        won = row["yes_won"]
    else:
        ask = row["no_ask"]
        won = 1 - row["yes_won"]
    return int(won), float(ask), ((1.0 if won else 0.0) - ask) / ask * stake


def _stats(trades: list[dict[str, Any]]) -> str:
    n = len(trades)
    wins = sum(t["won"] for t in trades)
    pnl = sum(t["pnl"] for t in trades)
    stake = sum(t["stake"] for t in trades)
    roi = pnl / stake * 100 if stake else 0.0
    return f"n={n} wins={wins}/{n} win={(wins/n*100 if n else 0):.1f}% pnl=${pnl:+.2f} roi={roi:+.1f}%"


def _eval_route(name: str, rows: list[dict[str, Any]], chooser) -> list[dict[str, Any]]:
    trades = []
    for row in rows:
        side = chooser(row)
        if side is None:
            continue
        won, ask, pnl = _pnl(side, row)
        prob = row["p_yes"] if side == "YES" else row["p_no"]
        trades.append(
            {
                **row,
                "side": side,
                "won": won,
                "ask": ask,
                "prob": prob,
                "edge": prob - ask,
                "pnl": pnl,
                "stake": 20.0,
            }
        )
    print(f"\n{name}: {_stats(trades)}")
    by_date = defaultdict(list)
    by_source = defaultdict(list)
    by_price = defaultdict(list)
    for trade in trades:
        by_date[trade["date"]].append(trade)
        by_source[trade["outcome_source"]].append(trade)
        bucket = f"{int(trade['ask'] * 10) / 10:.1f}-{int(trade['ask'] * 10) / 10 + 0.1:.1f}"
        by_price[bucket].append(trade)
    print("by_date=" + str({date: _stats(day) for date, day in sorted(by_date.items())}))
    print("by_source=" + str({source: _stats(src_trades) for source, src_trades in sorted(by_source.items())}))
    print("by_price=" + str({price: _stats(px_trades) for price, px_trades in sorted(by_price.items())}))
    for trade in trades[:20]:
        print(
            f"  {trade['date']} {trade['match_id']} {trade['side']} won={trade['won']} "
            f"ask={trade['ask']:.3f} p={trade['prob']:.3f} edge={trade['edge']:.3f} "
            f"lead={trade['radiant_lead']} gt={trade['game_time']} pnl=${trade['pnl']:+.2f} "
            f"{trade['name'][:65]}"
        )
    return trades


def main() -> None:
    started = time.time()
    scored = _load_scored_rows()
    rows = _candidate_rows(scored)
    nonbook_rows = _candidate_rows(scored, allow_final_book=False)
    print(
        f"SNAPSHOT ROUTE ANALYSIS rows={len(rows)} nonbook_rows={len(nonbook_rows)} "
        f"load_sec={time.time() - started:.1f}"
    )

    _eval_route("book favorite", rows, lambda row: row["book_side"])
    _eval_route("book favorite, no final-book labels", nonbook_rows, lambda row: row["book_side"])
    _eval_route(
        "book favorite, no final-book, mid-price only",
        nonbook_rows,
        lambda row: row["book_side"] if 0.50 <= (row["yes_ask"] if row["book_side"] == "YES" else row["no_ask"]) <= 0.80 else None,
    )
    _eval_route("model favorite", rows, lambda row: row["model_side"])
    _eval_route("book and model agree", rows, lambda row: row["book_side"] if row["book_side"] == row["model_side"] else None)
    _eval_route("model disagrees with book", rows, lambda row: row["model_side"] if row["book_side"] != row["model_side"] else None)
    _eval_route(
        "book+model agree, mid-price only",
        rows,
        lambda row: (
            row["book_side"]
            if row["book_side"] == row["model_side"]
            and 0.50 <= (row["yes_ask"] if row["book_side"] == "YES" else row["no_ask"]) <= 0.80
            else None
        ),
    )
    _eval_route(
        "model edge over ask >= 0.12",
        rows,
        lambda row: (
            "YES"
            if row["p_yes"] - row["yes_ask"] >= 0.12 and row["yes_ask"] >= 0.50
            else (
                "NO"
                if row["p_no"] - row["no_ask"] >= 0.12 and row["no_ask"] >= 0.50
                else None
            )
        ),
    )


if __name__ == "__main__":
    main()
