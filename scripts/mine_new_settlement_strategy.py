#!/usr/bin/env python3
"""Mine simple hold-to-settlement strategy rules from snapshots + book data.

This is research-only. It does not touch live config or order execution.
"""
from __future__ import annotations

import math
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import scripts.backtest_snapshot_winner_model as snap_model
import scripts.backtest_value_engine as value_bt
import winprob

STAKE = 20.0


def _side_to_yes(mapping: dict[str, Any], side: str) -> bool:
    normal = mapping.get("steam_side_mapping", "normal") == "normal"
    return (side == "radiant" and normal) or (side == "dire" and not normal)


def _side_token(mapping: dict[str, Any], side: str) -> str:
    return str(mapping["yes_token_id"] if _side_to_yes(mapping, side) else mapping["no_token_id"])


def _side_won(mapping: dict[str, Any], yes_won: int, side: str) -> int:
    return int(bool(yes_won) if _side_to_yes(mapping, side) else not bool(yes_won))


def _book_row(book: dict[str, tuple[list[int], list[dict]]], token: str, ns: int) -> dict | None:
    return value_bt.book_at(book, token, ns)


def _num(v: Any) -> float | None:
    try:
        x = float(v)
        if math.isnan(x):
            return None
        return x
    except Exception:
        return None


def _book_side(mapping: dict[str, Any], book: dict[str, tuple[list[int], list[dict]]], ns: int) -> tuple[str | None, float | None, float | None]:
    yes = _book_row(book, str(mapping["yes_token_id"]), ns)
    no = _book_row(book, str(mapping["no_token_id"]), ns)
    if not yes or not no:
        return None, None, None
    ya = _num(yes.get("best_ask"))
    na = _num(no.get("best_ask"))
    if ya is None or na is None:
        return None, None, None
    age = max((ns - int(yes["received_at_ns"])) / 1_000_000, (ns - int(no["received_at_ns"])) / 1_000_000)
    yes_is_radiant = _side_to_yes(mapping, "radiant")
    if ya >= na:
        return ("radiant" if yes_is_radiant else "dire"), ya, age
    return ("dire" if yes_is_radiant else "radiant"), na, age


def _ask_for_side(mapping: dict[str, Any], book: dict[str, tuple[list[int], list[dict]]], side: str, ns: int) -> tuple[float | None, float | None]:
    row = _book_row(book, _side_token(mapping, side), ns)
    if not row:
        return None, None
    ask = _num(row.get("best_ask"))
    if ask is None:
        return None, None
    return ask, (ns - int(row["received_at_ns"])) / 1_000_000


def _fair_for_side(row: dict[str, Any], side: str, lead: int, slope_rad: float) -> float:
    if side == "radiant":
        side_lead = lead
        elo = winprob.elo_diff(row.get("radiant_team_id"), row.get("dire_team_id"), row.get("radiant_team"), row.get("dire_team"))
        slope = slope_rad
    else:
        side_lead = -lead
        elo = winprob.elo_diff(row.get("dire_team_id"), row.get("radiant_team_id"), row.get("dire_team"), row.get("radiant_team"))
        slope = -slope_rad
    if side_lead >= 0:
        return winprob.fair(abs(side_lead), int(row["game_time_sec"]), elo, slope, None)
    opp_fair = winprob.fair(abs(side_lead), int(row["game_time_sec"]), elo, slope, None)
    return 1.0 - opp_fair


def build_events() -> list[dict[str, Any]]:
    outcomes, outcome_sources = value_bt.load_outcomes()
    markets, skipped = value_bt.load_markets()
    snapshots = value_bt.load_snapshots(set(markets))
    tokens = {str(m["yes_token_id"]) for m in markets.values()} | {str(m["no_token_id"]) for m in markets.values()}
    book = value_bt.load_books(tokens)
    joined = {
        match_id: rows
        for match_id, rows in snapshots.items()
        if str(markets[match_id]["yes_token_id"]) in book and str(markets[match_id]["no_token_id"]) in book
    }
    rows, coverage, unresolved = snap_model.build_rows(
        snapshots=joined,
        markets=markets,
        book=book,
        outcomes=outcomes,
        outcome_sources=outcome_sources,
    )
    scored_by_key = {
        (row["match_id"], int(row["game_time"])): row
        for row in snap_model.crossfit(rows)
    }

    events: list[dict[str, Any]] = []
    load_stats = Counter()
    for match_id, rows_for_match in joined.items():
        mapping = markets[match_id]
        yes_won, source = value_bt.resolve_yes_won(match_id, mapping, book, outcomes, outcome_sources)
        if yes_won is None:
            continue
        history: deque[tuple[int, int]] = deque(maxlen=4000)
        last_row: dict[str, Any] | None = None
        last_bucket: int | None = None
        for row in rows_for_match:
            ns = int(row.get("received_at_ns") or 0)
            gt = row.get("game_time_sec")
            lead = row.get("radiant_lead")
            if row.get("game_over") or gt is None or lead is None:
                continue
            gt = int(gt)
            if gt < 300 or gt > 3000:
                continue
            lead = int(lead)
            history.append((ns, lead))
            bucket = gt // 60
            if bucket == last_bucket:
                last_row = row
                continue
            last_bucket = bucket

            target = ns - 300_000_000_000
            past_lead = None
            for hist_ns, hist_lead in history:
                if hist_ns <= target:
                    past_lead = hist_lead
                else:
                    break
            slope_rad = 0.0 if past_lead is None else float(lead - past_lead)

            fav_side, fav_ask, fav_age = _book_side(mapping, book, ns)
            if fav_side is None or fav_ask is None or fav_age is None:
                load_stats["missing_book"] += 1
                last_row = row
                continue
            if fav_age > 15_000:
                load_stats["stale_book"] += 1
                last_row = row
                continue

            nw_side = "radiant" if lead > 0 else "dire" if lead < 0 else None
            score_side = None
            score_delta = None
            if last_row is not None and ns - int(last_row.get("received_at_ns") or 0) <= 120_000_000_000:
                dr = int(row.get("radiant_score") or 0) - int(last_row.get("radiant_score") or 0)
                dd = int(row.get("dire_score") or 0) - int(last_row.get("dire_score") or 0)
                score_delta = dr - dd
                if dr > dd:
                    score_side = "radiant"
                elif dd > dr:
                    score_side = "dire"

            model = scored_by_key.get((match_id, gt))
            p_yes = model.get("p_yes") if model else None
            model_side = None
            if p_yes is not None:
                model_yes_side = "radiant" if _side_to_yes(mapping, "radiant") else "dire"
                model_no_side = "dire" if model_yes_side == "radiant" else "radiant"
                model_side = model_yes_side if p_yes >= 0.5 else model_no_side

            for side_name, side in [
                ("fav", fav_side),
                ("nw", nw_side),
                ("score", score_side),
                ("model", model_side),
            ]:
                if side not in {"radiant", "dire"}:
                    continue
                ask, age = _ask_for_side(mapping, book, side, ns)
                if ask is None or age is None or age > 15_000:
                    continue
                fair = _fair_for_side(row, side, lead, slope_rad)
                won = _side_won(mapping, yes_won, side)
                events.append(
                    {
                        "date": str(row.get("date")),
                        "match_id": match_id,
                        "name": mapping.get("name", ""),
                        "source": source,
                        "side_name": side_name,
                        "side": side,
                        "won": won,
                        "ask": ask,
                        "pnl": ((1.0 if won else 0.0) - ask) / ask * STAKE,
                        "gt": gt,
                        "minute": gt / 60.0,
                        "lead": lead,
                        "abs_lead": abs(lead),
                        "slope_rad": slope_rad,
                        "side_slope": slope_rad if side == "radiant" else -slope_rad,
                        "kill_lead": int(row.get("radiant_score") or 0) - int(row.get("dire_score") or 0),
                        "score_delta": score_delta,
                        "fair": fair,
                        "edge": fair - ask,
                        "fav_side": fav_side,
                        "fav_ask": fav_ask,
                        "fav_gap": abs((fav_ask or 0.0) - (1.0 - (fav_ask or 0.0))),
                        "same_as_fav": side == fav_side,
                        "same_as_nw": side == nw_side,
                        "same_as_score": score_side is not None and side == score_side,
                        "score_nw_opposed": score_side is not None and nw_side is not None and score_side != nw_side,
                        "model_side": model_side,
                        "same_as_model": model_side is not None and side == model_side,
                    }
                )
            last_row = row

    print(
        f"loaded markets={len(markets)} skipped={dict(skipped)} resolved={sum(coverage.values())} "
        f"unresolved={len(unresolved)} joined={len(joined)} raw_events={len(events)} load_rejects={dict(load_stats)}"
    )
    return events


def summarize(label: str, trades: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(trades)
    wins = sum(t["won"] for t in trades)
    pnl = sum(t["pnl"] for t in trades)
    stake = STAKE * n
    roi = pnl / stake * 100 if stake else 0.0
    return {"label": label, "n": n, "wins": wins, "win": wins / n if n else 0.0, "pnl": pnl, "roi": roi}


def first_per_match(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in sorted(rows, key=lambda x: (x["date"], x["match_id"], x["gt"])):
        if row["match_id"] in seen:
            continue
        seen.add(row["match_id"])
        out.append(row)
    return out


Rule = tuple[str, Callable[[dict[str, Any]], bool]]


def mine(events: list[dict[str, Any]]) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    rules: list[Rule] = []
    for side_name in ["fav", "nw", "model", "score"]:
        rules.append((f"{side_name}", lambda r, s=side_name: r["side_name"] == s))
    for min_gt, max_gt in [(300, 900), (600, 1200), (900, 1800), (1200, 2400), (1800, 3000)]:
        rules.append((f"time_{min_gt}_{max_gt}", lambda r, a=min_gt, b=max_gt: a <= r["gt"] <= b))
    for lo, hi in [(0.20, 0.50), (0.35, 0.65), (0.50, 0.75), (0.55, 0.84), (0.65, 0.84)]:
        rules.append((f"ask_{lo:.2f}_{hi:.2f}", lambda r, a=lo, b=hi: a <= r["ask"] <= b))
    for min_lead in [0, 1000, 2000, 3000, 5000, 8000]:
        rules.append((f"abslead_ge_{min_lead}", lambda r, x=min_lead: r["abs_lead"] >= x))
    for min_edge in [-0.05, 0.00, 0.03, 0.07, 0.10, 0.15]:
        rules.append((f"edge_ge_{min_edge:.2f}", lambda r, x=min_edge: r["edge"] >= x))
    for min_fair in [0.50, 0.55, 0.60, 0.65, 0.70, 0.75]:
        rules.append((f"fair_ge_{min_fair:.2f}", lambda r, x=min_fair: r["fair"] >= x))
    rules.extend(
        [
            ("same_as_fav", lambda r: r["same_as_fav"]),
            ("not_fav", lambda r: not r["same_as_fav"]),
            ("same_as_nw", lambda r: r["same_as_nw"]),
            ("same_as_model", lambda r: r["same_as_model"]),
            ("score_nw_opposed", lambda r: r["score_nw_opposed"]),
            ("side_slope_pos", lambda r: r["side_slope"] > 0),
            ("side_slope_ge_1000", lambda r: r["side_slope"] >= 1000),
            ("side_slope_le_neg1000", lambda r: r["side_slope"] <= -1000),
            ("non_final_book", lambda r: r["source"] != "final_book_mid"),
        ]
    )

    candidates: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    base_sides = [r for r in rules if r[0] in {"fav", "nw", "model", "score"}]
    filters = [r for r in rules if r not in base_sides]
    for side_label, side_pred in base_sides:
        for f1_label, f1 in filters:
            for f2_label, f2 in [(None, lambda r: True)] + filters:
                if f2_label == f1_label:
                    continue
                label = " + ".join([x for x in [side_label, f1_label, f2_label] if x])
                rows = first_per_match([r for r in events if side_pred(r) and f1(r) and f2(r)])
                if len(rows) < 12:
                    continue
                s = summarize(label, rows)
                by_source = defaultdict(list)
                by_date = defaultdict(list)
                for row in rows:
                    by_source[row["source"]].append(row)
                    by_date[row["date"]].append(row)
                s["sources"] = {k: summarize(k, v)["roi"] for k, v in by_source.items()}
                s["dates"] = {k: summarize(k, v)["roi"] for k, v in by_date.items()}
                s["avg_ask"] = sum(r["ask"] for r in rows) / len(rows)
                s["fav_overlap"] = sum(r["same_as_fav"] for r in rows) / len(rows)
                candidates.append((s, rows))
    candidates.sort(key=lambda x: (x[0]["roi"], x[0]["pnl"], x[0]["n"]), reverse=True)
    return candidates


def main() -> None:
    events = build_events()
    candidates = mine(events)
    print("\nTOP CANDIDATES n>=12, one entry per match")
    shown = 0
    for s, rows in candidates:
        # Avoid top list full of tiny final-book-only variants.
        if s["n"] < 12:
            continue
        shown += 1
        print(
            f"\n#{shown} {s['label']} n={s['n']} wins={s['wins']}/{s['n']} "
            f"win={s['win']*100:.1f}% pnl=${s['pnl']:+.2f} roi={s['roi']:+.1f}% "
            f"avg_ask={s['avg_ask']:.3f} fav_overlap={s['fav_overlap']*100:.0f}%"
        )
        print(f"  sources_roi={s['sources']}")
        print(f"  dates_roi={s['dates']}")
        for row in rows[:8]:
            print(
                f"  {row['date']} {row['match_id']} {row['side_name']} {row['side']} "
                f"won={row['won']} ask={row['ask']:.3f} fair={row['fair']:.3f} "
                f"edge={row['edge']:.3f} gt={row['gt']} lead={row['lead']} "
                f"slope={row['side_slope']:.0f} pnl=${row['pnl']:+.2f} {row['name'][:58]}"
            )
        if shown >= 25:
            break

    print("\nBASELINES")
    for side in ["fav", "nw", "model", "score"]:
        rows = first_per_match([r for r in events if r["side_name"] == side and 0.20 <= r["ask"] <= 0.84])
        s = summarize(side, rows)
        print(
            f"{side:>6}: n={s['n']} wins={s['wins']}/{s['n']} "
            f"win={s['win']*100:.1f}% pnl=${s['pnl']:+.2f} roi={s['roi']:+.1f}%"
        )


if __name__ == "__main__":
    main()
