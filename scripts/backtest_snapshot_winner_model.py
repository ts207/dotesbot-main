#!/usr/bin/env python3
"""Cross-fitted snapshot winner model backtest.

This asks a different question than VALUE: can game-state snapshots predict the
settlement winner often enough to create more executable trades? Each date is
scored by a model trained on the other dates to reduce direct leakage.
"""
from __future__ import annotations

import bisect
import math
import sys
import time
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import scripts.backtest_value_engine as value_bt
import winprob


FEATURES = [
    "game_time",
    "minute",
    "radiant_lead",
    "abs_lead",
    "lead_per_min",
    "lead_slope_5m",
    "elo_diff_rad",
    "fair_rad",
]


def _radiant_fair(row: dict[str, Any], lead: int, slope_rad: float) -> tuple[float, float]:
    elo_rad = winprob.elo_diff(
        row.get("radiant_team_id"),
        row.get("dire_team_id"),
        row.get("radiant_team"),
        row.get("dire_team"),
    )
    if lead >= 0:
        fair = winprob.fair(abs(lead), int(row["game_time_sec"]), elo_rad, slope_rad, None)
    else:
        elo_dire = winprob.elo_diff(
            row.get("dire_team_id"),
            row.get("radiant_team_id"),
            row.get("dire_team"),
            row.get("radiant_team"),
        )
        fair_dire = winprob.fair(abs(lead), int(row["game_time_sec"]), elo_dire, -slope_rad, None)
        fair = 1.0 - fair_dire
    return fair, float(elo_rad or 0.0)


def _book_after_or_at(book: dict[str, tuple[list[int], list[dict]]], token_id: str, ns: int) -> dict | None:
    item = book.get(str(token_id))
    if not item:
        return None
    times, rows = item
    idx = bisect.bisect_right(times, ns) - 1
    if idx < 0:
        return None
    return rows[idx]


def build_rows(
    *,
    snapshots: dict[str, list[dict]],
    markets: dict[str, dict],
    book: dict[str, tuple[list[int], list[dict]]],
    outcomes: dict[str, bool],
    outcome_sources: dict[str, str],
    sample_sec: int = 60,
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
        radiant_win = outcomes.get(match_id)
        if radiant_win is None:
            # final-book fallback tells us YES/NO, not necessarily radiant/dire.
            side_map = mapping.get("steam_side_mapping", "normal")
            if side_map == "normal":
                radiant_win = bool(yes_won)
            elif side_map == "reversed":
                radiant_win = not bool(yes_won)
            else:
                continue

        coverage[source] += 1
        history: deque[tuple[int, int]] = deque(maxlen=4000)
        last_bucket: int | None = None

        for snap in snaps:
            ns = int(snap.get("received_at_ns") or 0)
            game_time = snap.get("game_time_sec")
            lead = snap.get("radiant_lead")
            if snap.get("game_over") or game_time is None or lead is None:
                continue
            game_time = int(game_time)
            if game_time < 0:
                continue
            bucket = game_time // sample_sec
            if bucket == last_bucket:
                continue
            last_bucket = bucket

            lead = int(lead)
            history.append((ns, lead))
            target = ns - 300_000_000_000
            past = None
            for hist_ns, hist_lead in history:
                if hist_ns <= target:
                    past = hist_lead
                else:
                    break
            slope_rad = 0.0 if past is None else float(lead - past)
            fair_rad, elo_rad = _radiant_fair(snap, lead, slope_rad)

            yes_book = _book_after_or_at(book, yes_token, ns)
            no_book = _book_after_or_at(book, no_token, ns)
            if not yes_book or not no_book:
                continue
            yes_ask = yes_book.get("best_ask")
            no_ask = no_book.get("best_ask")
            if yes_ask is None or no_ask is None:
                continue
            yes_age = (ns - int(yes_book["received_at_ns"])) / 1_000_000
            no_age = (ns - int(no_book["received_at_ns"])) / 1_000_000

            rows.append(
                {
                    "date": str(snap.get("date")),
                    "match_id": match_id,
                    "name": mapping.get("name", ""),
                    "steam_side_mapping": mapping.get("steam_side_mapping", "normal"),
                    "yes_token_id": yes_token,
                    "no_token_id": no_token,
                    "yes_won": int(yes_won),
                    "radiant_win": int(bool(radiant_win)),
                    "game_time": game_time,
                    "minute": game_time / 60.0,
                    "radiant_lead": lead,
                    "abs_lead": abs(lead),
                    "lead_per_min": lead / max(game_time / 60.0, 1.0),
                    "lead_slope_5m": slope_rad,
                    "elo_diff_rad": elo_rad,
                    "fair_rad": fair_rad,
                    "yes_ask": float(yes_ask),
                    "no_ask": float(no_ask),
                    "yes_age_ms": float(yes_age),
                    "no_age_ms": float(no_age),
                    "outcome_source": source,
                }
            )

    rows.sort(key=lambda row: (row["date"], row["match_id"], row["game_time"]))
    return rows, coverage, unresolved


def crossfit(rows: list[dict]) -> list[dict]:
    dates = sorted({row["date"] for row in rows})
    out: list[dict] = []
    for date in dates:
        train = [row for row in rows if row["date"] != date]
        test = [row for row in rows if row["date"] == date]
        if len({row["radiant_win"] for row in train}) < 2:
            continue
        x_train = np.array([[row[f] for f in FEATURES] for row in train], dtype=float)
        y_train = np.array([row["radiant_win"] for row in train], dtype=int)
        x_test = np.array([[row[f] for f in FEATURES] for row in test], dtype=float)
        model = make_pipeline(
            StandardScaler(),
            LogisticRegression(C=0.35, max_iter=1000, class_weight="balanced"),
        )
        model.fit(x_train, y_train)
        prob_rad = model.predict_proba(x_test)[:, 1]
        for row, p_rad in zip(test, prob_rad):
            side_map = row["steam_side_mapping"]
            if side_map == "normal":
                p_yes = float(p_rad)
            elif side_map == "reversed":
                p_yes = 1.0 - float(p_rad)
            else:
                continue
            out.append({**row, "p_radiant": float(p_rad), "p_yes": p_yes, "p_no": 1.0 - p_yes})
    out.sort(key=lambda row: (row["date"], row["match_id"], row["game_time"]))
    return out


def simulate(rows: list[dict], rule: dict[str, Any]) -> list[dict]:
    trades: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if row["match_id"] in seen:
            continue
        if row["game_time"] < rule["min_time"] or row["game_time"] > rule["max_time"]:
            continue
        if max(row["yes_age_ms"], row["no_age_ms"]) > rule["max_book_age_ms"]:
            continue

        candidates = [
            ("YES", row["p_yes"], row["yes_ask"], row["yes_won"]),
            ("NO", row["p_no"], row["no_ask"], 1 - row["yes_won"]),
        ]
        side, prob, ask, won = max(candidates, key=lambda item: item[1] - item[2])
        edge = prob - ask
        if ask < rule["min_ask"] or ask > rule["max_ask"]:
            continue
        if prob < rule["min_prob"]:
            continue
        if edge < rule["min_edge"] or edge > rule["max_edge"]:
            continue

        stake = float(rule["stake"])
        pnl = ((1.0 if won else 0.0) - ask) / ask * stake
        trades.append(
            {
                **row,
                "side": side,
                "prob": prob,
                "ask": ask,
                "edge": edge,
                "won": int(won),
                "stake": stake,
                "pnl": pnl,
            }
        )
        seen.add(row["match_id"])
    return trades


def prediction_diagnostics(rows: list[dict]) -> None:
    eligible = [
        row
        for row in rows
        if row["game_time"] >= 600
        and row["game_time"] <= 2400
        and max(row["yes_age_ms"], row["no_age_ms"]) <= 15000
    ]
    first_by_match: dict[str, dict] = {}
    for row in eligible:
        first_by_match.setdefault(row["match_id"], row)

    def acc(sample: list[dict], pred_key: str) -> float:
        if not sample:
            return 0.0
        return sum(1 for row in sample if int(row[pred_key]) == int(row["yes_won"])) / len(sample)

    for sample_name, sample in [("eligible_rows", eligible), ("first_per_match", list(first_by_match.values()))]:
        enriched = []
        for row in sample:
            model_yes = int(row["p_yes"] >= 0.5)
            book_yes = int(row["yes_ask"] >= row["no_ask"])
            enriched.append({**row, "model_yes": model_yes, "book_yes": book_yes})
        print(
            f"{sample_name}: n={len(enriched)} "
            f"model_acc={acc(enriched, 'model_yes')*100:.1f}% "
            f"book_fav_acc={acc(enriched, 'book_yes')*100:.1f}%"
        )


def stats(trades: list[dict]) -> dict[str, float]:
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


def fmt(s: dict[str, float]) -> str:
    n = int(s["n"])
    wins = int(s["wins"])
    return f"n={n} wins={wins}/{n} win={s['win_pct']*100:.1f}% pnl=${s['pnl']:+.2f} roi={s['roi']*100:+.1f}%"


def main() -> None:
    started = time.time()
    outcomes, outcome_sources = value_bt.load_outcomes()
    markets, skipped = value_bt.load_markets()
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
    rows, coverage, unresolved = build_rows(
        snapshots=joined,
        markets=markets,
        book=book,
        outcomes=outcomes,
        outcome_sources=outcome_sources,
    )
    scored = crossfit(rows)

    print("SNAPSHOT WINNER MODEL BACKTEST")
    print(
        f"valid_markets={len(markets)} skipped={dict(skipped)} joined={len(joined)} "
        f"resolved={sum(coverage.values())} unresolved={len(unresolved)} "
        f"snapshot_rows={len(rows)} scored_rows={len(scored)} dates={sorted({r['date'] for r in scored})} "
        f"load_sec={time.time() - started:.1f}"
    )
    print(f"coverage_sources={dict(coverage)} features={FEATURES}")
    prediction_diagnostics(scored)

    rules = []
    for min_prob in [0.62, 0.66, 0.70, 0.74]:
        for min_edge in [0.08, 0.10, 0.12, 0.15]:
            for max_edge in [0.25, 0.30, 0.40]:
                for min_ask in [0.45, 0.50, 0.55]:
                    for max_ask in [0.78, 0.84, 0.90]:
                        rules.append(
                            {
                                "min_prob": min_prob,
                                "min_edge": min_edge,
                                "max_edge": max_edge,
                                "min_ask": min_ask,
                                "max_ask": max_ask,
                                "min_time": 600,
                                "max_time": 2400,
                                "max_book_age_ms": 15000,
                                "stake": 20.0,
                            }
                        )

    ranked = []
    all_ranked = []
    for rule in rules:
        trades = simulate(scored, rule)
        st = stats(trades)
        if st["n"] >= 5:
            all_ranked.append((st["pnl"] + 20 * st["roi"], rule, st, trades))
        if st["n"] < 10 or st["pnl"] <= 0:
            continue
        by_date = defaultdict(list)
        for trade in trades:
            by_date[trade["date"]].append(trade)
        if len(by_date) < 4:
            continue
        losing_days = sum(1 for day_trades in by_date.values() if stats(day_trades)["pnl"] < 0)
        score = st["pnl"] + 40 * st["roi"] - 3 * losing_days
        ranked.append((score, rule, st, trades, by_date))
    ranked.sort(key=lambda item: (item[0], item[2]["pnl"], item[2]["n"]), reverse=True)
    all_ranked.sort(key=lambda item: (item[0], item[2]["pnl"], item[2]["n"]), reverse=True)

    if not ranked:
        print("No positive cross-fitted snapshot winner rule survived.")
        print("\nLeast-bad loose rules:")
        for rank, (_score, rule, st, trades) in enumerate(all_ranked[:5], 1):
            print(f"#{rank} rule={rule} all={fmt(st)}")
            for trade in trades[:12]:
                print(
                    f"  {trade['date']} {trade['match_id']} {trade['side']} won={trade['won']} "
                    f"ask={trade['ask']:.3f} p={trade['prob']:.3f} edge={trade['edge']:.3f} "
                    f"lead={trade['radiant_lead']} gt={trade['game_time']} pnl=${trade['pnl']:+.2f} "
                    f"{trade['name'][:70]}"
                )
        return

    for rank, (score, rule, st, trades, by_date) in enumerate(ranked[:10], 1):
        print(f"\n#{rank} score={score:.2f}")
        print(f"rule={rule}")
        print(f"all={fmt(st)}")
        print("by_date=" + str({date: fmt(stats(day_trades)) for date, day_trades in sorted(by_date.items())}))
        for trade in trades[:25]:
            print(
                f"  {trade['date']} {trade['match_id']} {trade['side']} won={trade['won']} "
                f"ask={trade['ask']:.3f} p={trade['prob']:.3f} edge={trade['edge']:.3f} "
                f"lead={trade['radiant_lead']} gt={trade['game_time']} pnl=${trade['pnl']:+.2f} "
                f"{trade['name'][:70]}"
            )


if __name__ == "__main__":
    main()
