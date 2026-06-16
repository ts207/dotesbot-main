#!/usr/bin/env python3
"""Mine simple hold-to-settlement strategies from local snapshots and books.

This is intentionally a research tool. It uses the same corrected loaders as
backtest_value_engine.py, but precomputes executable opportunities and searches
for rules that survive a date split instead of just optimizing total PnL.
"""
from __future__ import annotations

import itertools
import sys
import time
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import scripts.backtest_value_engine as value_bt


def _opportunity_rows(
    *,
    snapshots: dict[str, list[dict]],
    markets: dict[str, dict],
    book: dict[str, tuple[list[int], list[dict]]],
    outcomes: dict[str, bool],
    outcome_sources: dict[str, str],
) -> tuple[list[dict], Counter, list[tuple[str, str, str]]]:
    rows: list[dict] = []
    coverage: Counter = Counter()
    unresolved: list[tuple[str, str, str]] = []

    for match_id, snaps in snapshots.items():
        mapping = markets[match_id]
        yes_token = str(mapping["yes_token_id"])
        no_token = str(mapping["no_token_id"])
        yes_won, source = value_bt.resolve_yes_won(match_id, mapping, book, outcomes, outcome_sources)
        if yes_won is None:
            unresolved.append((match_id, source, mapping.get("name", "")))
            continue

        coverage[source] += 1
        history: deque[tuple[int, int]] = deque(maxlen=4000)
        for snap in snaps:
            ns = int(snap.get("received_at_ns") or 0)
            game_time = snap.get("game_time_sec")
            lead = snap.get("radiant_lead")
            if snap.get("game_over") or game_time is None or lead is None:
                continue
            game_time = int(game_time)
            if game_time < 0:
                continue
            lead = int(lead)
            if lead == 0:
                continue

            history.append((ns, lead))
            side, direction = value_bt.signal_side(mapping, lead)
            if side is None:
                continue
            token = yes_token if side == "YES" else no_token
            entry_book = value_bt.book_at(book, token, ns)
            if not entry_book:
                continue
            ask = entry_book.get("best_ask")
            if ask is None:
                continue
            ask = float(ask)
            if ask <= 0.01 or ask >= 0.99:
                continue

            book_age_ms = (ns - int(entry_book["received_at_ns"])) / 1_000_000
            fair = value_bt.fair_price(snap, direction, lead, history)
            edge = fair - ask
            token_won = yes_won if token == yes_token else 1 - yes_won

            rows.append(
                {
                    "date": str(snap.get("date")),
                    "match_id": match_id,
                    "name": mapping.get("name", ""),
                    "side": side,
                    "direction": direction,
                    "token_id": token,
                    "won": int(token_won),
                    "ask": ask,
                    "fair": fair,
                    "edge": edge,
                    "lead_abs": abs(lead),
                    "lead": lead,
                    "game_time": game_time,
                    "book_age_ms": float(book_age_ms),
                    "outcome_source": source,
                }
            )

    rows.sort(key=lambda row: (row["date"], row["match_id"], row["game_time"]))
    return rows, coverage, unresolved


def _passes(row: dict[str, Any], rule: dict[str, Any]) -> bool:
    return (
        row["book_age_ms"] <= rule["max_book_age_ms"]
        and row["game_time"] >= rule["min_time"]
        and row["game_time"] <= rule["max_time"]
        and row["lead_abs"] >= rule["min_lead"]
        and row["ask"] >= rule["min_ask"]
        and row["ask"] <= rule["max_ask"]
        and row["fair"] >= rule["min_fair"]
        and row["edge"] >= rule["min_edge"]
        and row["edge"] <= rule["max_edge"]
    )


def _simulate(opportunities: list[dict], rule: dict[str, Any], dates: set[str] | None = None) -> list[dict]:
    selected: list[dict] = []
    seen: set[str] = set()
    for row in opportunities:
        if dates is not None and row["date"] not in dates:
            continue
        if row["match_id"] in seen:
            continue
        if not _passes(row, rule):
            continue
        stake = float(rule["stake"])
        pnl = ((1.0 if row["won"] else 0.0) - row["ask"]) / row["ask"] * stake
        selected.append({**row, "stake": stake, "pnl": pnl})
        seen.add(row["match_id"])
    return selected


def _stats(trades: list[dict]) -> dict[str, float]:
    n = len(trades)
    wins = sum(t["won"] for t in trades)
    pnl = sum(t["pnl"] for t in trades)
    stake = sum(t["stake"] for t in trades)
    return {
        "n": n,
        "wins": wins,
        "pnl": pnl,
        "stake": stake,
        "roi": pnl / stake if stake else 0.0,
        "win_pct": wins / n if n else 0.0,
    }


def _rule_grid() -> list[dict[str, Any]]:
    grid = {
        "min_edge": [0.10, 0.12, 0.15, 0.18],
        "max_edge": [0.25, 0.30, 0.35],
        "min_fair": [0.66, 0.70, 0.74],
        "min_ask": [0.50, 0.55, 0.60],
        "max_ask": [0.78, 0.82, 0.86],
        "min_time": [600, 900],
        "max_time": [1800, 2400],
        "min_lead": [0, 1000, 2000],
    }
    rules = []
    keys = list(grid)
    for values in itertools.product(*(grid[k] for k in keys)):
        rule = dict(zip(keys, values))
        if rule["min_ask"] > rule["max_ask"]:
            continue
        if rule["min_edge"] > rule["max_edge"]:
            continue
        rule["max_book_age_ms"] = 15000
        rule["stake"] = 20.0
        rules.append(rule)
    return rules


def _format_stats(stats: dict[str, float]) -> str:
    n = int(stats["n"])
    wins = int(stats["wins"])
    return (
        f"n={n} wins={wins}/{n} win={stats['win_pct']*100:.1f}% "
        f"pnl=${stats['pnl']:+.2f} roi={stats['roi']*100:+.1f}%"
    )


def main() -> None:
    started = time.time()
    outcomes, outcome_sources = value_bt.load_outcomes()
    markets, skipped = value_bt.load_markets()
    snapshots = value_bt.load_snapshots(set(markets))

    tokens: set[str] = set()
    for match_id in snapshots:
        tokens.add(str(markets[match_id]["yes_token_id"]))
        tokens.add(str(markets[match_id]["no_token_id"]))
    book = value_bt.load_books(tokens)
    joined = {
        match_id: rows
        for match_id, rows in snapshots.items()
        if str(markets[match_id]["yes_token_id"]) in book
        and str(markets[match_id]["no_token_id"]) in book
    }

    opportunities, coverage, unresolved = _opportunity_rows(
        snapshots=joined,
        markets=markets,
        book=book,
        outcomes=outcomes,
        outcome_sources=outcome_sources,
    )
    dates = sorted({row["date"] for row in opportunities})
    if len(dates) >= 2:
        train_dates = set(dates[:-1])
        test_dates = {dates[-1]}
    else:
        train_dates = set(dates)
        test_dates = set(dates)

    candidates = []
    for rule in _rule_grid():
        all_trades = _simulate(opportunities, rule)
        train = [trade for trade in all_trades if trade["date"] in train_dates]
        test = [trade for trade in all_trades if trade["date"] in test_dates]
        train_stats = _stats(train)
        test_stats = _stats(test)
        all_stats = _stats(all_trades)
        if train_stats["n"] < 8 or all_stats["n"] < 10:
            continue
        if train_stats["pnl"] <= 0 or train_stats["roi"] <= 0.05:
            continue
        if test_stats["n"] and test_stats["pnl"] < -20:
            continue
        score = (
            all_stats["pnl"]
            + 25.0 * min(test_stats["roi"], 0.25)
            + 10.0 * min(train_stats["roi"], 0.25)
            - max(0, 12 - test_stats["n"]) * 1.5
        )
        candidates.append((score, rule, train_stats, test_stats, all_stats, all_trades))

    candidates.sort(key=lambda item: (item[0], item[4]["pnl"], item[4]["n"]), reverse=True)

    print("SETTLEMENT STRATEGY MINER")
    print(
        f"valid_markets={len(markets)} skipped={dict(skipped)} joined_matches={len(joined)} "
        f"resolved={sum(coverage.values())} unresolved={len(unresolved)} "
        f"opportunities={len(opportunities)} dates={dates} load_sec={time.time() - started:.1f}"
    )
    print(f"coverage_sources={dict(coverage)}")
    print(f"train_dates={sorted(train_dates)} test_dates={sorted(test_dates)}")

    if not candidates:
        print("No candidate survived the minimum sample and split filters.")
        return

    for rank, (score, rule, train_stats, test_stats, all_stats, trades) in enumerate(candidates[:10], 1):
        print(f"\n#{rank} score={score:.2f}")
        print(f"rule={rule}")
        print(f"train: {_format_stats(train_stats)}")
        print(f"test : {_format_stats(test_stats)}")
        print(f"all  : {_format_stats(all_stats)}")
        by_date: dict[str, list[dict]] = defaultdict(list)
        for trade in trades:
            by_date[trade["date"]].append(trade)
        print(
            "by_date="
            + str(
                {
                    date: _format_stats(_stats(day_trades))
                    for date, day_trades in sorted(by_date.items())
                }
            )
        )
        for trade in trades[:20]:
            print(
                f"  {trade['date']} {trade['match_id']} {trade['side']} won={trade['won']} "
                f"ask={trade['ask']:.3f} fair={trade['fair']:.3f} edge={trade['edge']:.3f} "
                f"lead={trade['lead']} gt={trade['game_time']} pnl=${trade['pnl']:+.2f} "
                f"{trade['name'][:70]}"
            )


if __name__ == "__main__":
    main()
