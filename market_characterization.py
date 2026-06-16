#!/usr/bin/env python3
"""Characterize Dota/Polymarket market structure from local historical data.

This script is intentionally diagnostic only. It runs four tests:
1. Reaction speed from Steam NW deltas to Polymarket ask changes.
2. Spread behavior by game-minute bucket and Roshan window.
3. Market mid R2 decomposition: raw state vs raw state + game fixed effects.
4. Post-draft/early-market residual calibration proxies.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any, Iterable

import numpy as np
import yaml
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score
from sklearn.preprocessing import OneHotEncoder, PolynomialFeatures


@dataclass(frozen=True)
class MarketMapping:
    match_id: str
    market_name: str
    market_type: str
    yes_token_id: str
    no_token_id: str


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def fnum(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return x


def pct(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile / 100.0
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return ordered[lo]
    w = rank - lo
    return ordered[lo] * (1 - w) + ordered[hi] * w


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def load_mappings(path: Path, market_type: str | None) -> dict[str, MarketMapping]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: dict[str, MarketMapping] = {}
    for row in data.get("markets", []):
        match_id = str(row.get("dota_match_id") or "")
        if not match_id or not match_id.isdigit():
            continue
        mtype = str(row.get("market_type") or "")
        if market_type and mtype != market_type:
            continue
        yes = str(row.get("yes_token_id") or "")
        no = str(row.get("no_token_id") or "")
        if not yes or not no or "TOKEN_ID_HERE" in yes or "TOKEN_ID_HERE" in no:
            continue
        out[match_id] = MarketMapping(
            match_id=match_id,
            market_name=str(row.get("name") or ""),
            market_type=mtype,
            yes_token_id=yes,
            no_token_id=no,
        )
    return out


def load_match_index(path: Path, market_type: str | None) -> list[dict[str, str]]:
    rows = read_csv(path)
    selected = []
    for row in rows:
        if row.get("has_book") != "1":
            continue
        if row.get("yes_won") not in {"0", "1"}:
            continue
        if market_type and row.get("market_type") != market_type:
            continue
        selected.append(row)
    return selected


def clean_market_rows(clean_dir: Path, match_rows: list[dict[str, str]]) -> list[dict[str, float | str]]:
    out: list[dict[str, float | str]] = []
    for match in match_rows:
        match_id = str(match["match_id"])
        path = clean_dir / "snapshots" / f"{match_id}.csv"
        if not path.exists():
            continue
        for row in read_csv(path):
            bid = fnum(row.get("yes_bid"))
            ask = fnum(row.get("yes_ask"))
            minute = fnum(row.get("gt_min"))
            nw = fnum(row.get("yes_nw_lead"))
            kills = fnum(row.get("yes_kill_lead"))
            spread = fnum(row.get("yes_spread"))
            if bid is None or ask is None or minute is None or nw is None or kills is None:
                continue
            if ask < bid:
                continue
            out.append(
                {
                    "match_id": match_id,
                    "minute": minute,
                    "nw_diff": nw,
                    "kill_diff": kills,
                    "market_mid": (bid + ask) / 2.0,
                    "spread": spread if spread is not None else ask - bid,
                    "yes_won": float(match["yes_won"]),
                    "duration_min": fnum(match.get("duration_min")) or 0.0,
                }
            )
    return out


def test1_reaction_speed(
    raw_snapshots_path: Path,
    book_events_path: Path,
    mappings: dict[str, MarketMapping],
    *,
    nw_delta_min: float,
    ask_move_min: float,
    reaction_window_sec: float,
) -> dict[str, Any]:
    books_by_asset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    needed_assets = {m.yes_token_id for m in mappings.values()}
    for row in read_csv(book_events_path):
        asset_id = str(row.get("asset_id") or "")
        if asset_id not in needed_assets:
            continue
        ts = parse_ts(row.get("timestamp_utc"))
        ask = fnum(row.get("best_ask"))
        if ts is None or ask is None:
            continue
        books_by_asset[asset_id].append({"ts": ts, "ask": ask})
    for rows in books_by_asset.values():
        rows.sort(key=lambda r: r["ts"])

    snapshots_by_match: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_csv(raw_snapshots_path):
        match_id = str(row.get("match_id") or "")
        if match_id not in mappings:
            continue
        ts = parse_ts(row.get("received_at_utc"))
        lead = fnum(row.get("radiant_lead"))
        gt = fnum(row.get("game_time_sec"))
        if ts is None or lead is None or gt is None:
            continue
        snapshots_by_match[match_id].append({"ts": ts, "lead": lead, "game_time_sec": gt})
    for rows in snapshots_by_match.values():
        rows.sort(key=lambda r: r["ts"])

    lags: list[float] = []
    events = 0
    no_move = 0
    segmented: dict[str, list[float]] = defaultdict(list)

    for match_id, snaps in snapshots_by_match.items():
        mapping = mappings[match_id]
        books = books_by_asset.get(mapping.yes_token_id, [])
        if not books:
            continue
        book_idx = 0
        prev_lead: float | None = None
        for snap in snaps:
            lead = float(snap["lead"])
            if prev_lead is None:
                prev_lead = lead
                continue
            delta = lead - prev_lead
            prev_lead = lead
            if abs(delta) < nw_delta_min:
                continue
            while book_idx < len(books) and books[book_idx]["ts"] <= snap["ts"]:
                book_idx += 1
            base_idx = book_idx - 1
            if base_idx < 0:
                continue
            base_ask = float(books[base_idx]["ask"])
            events += 1
            found: float | None = None
            scan = book_idx
            while scan < len(books):
                dt = (books[scan]["ts"] - snap["ts"]).total_seconds()
                if dt < 0:
                    scan += 1
                    continue
                if dt > reaction_window_sec:
                    break
                if abs(float(books[scan]["ask"]) - base_ask) >= ask_move_min:
                    found = dt
                    break
                scan += 1
            if found is None:
                no_move += 1
                continue
            lags.append(found)
            minute = float(snap["game_time_sec"]) / 60.0
            mag = abs(delta)
            if minute < 10:
                segmented["game_time_early"].append(found)
            elif minute < 30:
                segmented["game_time_mid"].append(found)
            else:
                segmented["game_time_late"].append(found)
            if mag < 1000:
                segmented["magnitude_small"].append(found)
            elif mag < 3000:
                segmented["magnitude_medium"].append(found)
            else:
                segmented["magnitude_large"].append(found)

    def under(sec: float) -> float | None:
        return sum(x < sec for x in lags) / events if events else None

    def over(sec: float) -> float | None:
        if not events:
            return None
        # Missing moves are right-censored beyond the reaction window and count
        # as residual stale/no-reprice opportunities for event-level rates.
        return (sum(x > sec for x in lags) + no_move) / events

    return {
        "events": events,
        "moves_within_window": len(lags),
        "no_move_within_window": no_move,
        "lag_median": median(lags) if lags else None,
        "lag_p75": pct(lags, 75),
        "lag_p90": pct(lags, 90),
        "lag_p95": pct(lags, 95),
        "lag_p99": pct(lags, 99),
        "pct_under_30s": under(30),
        "pct_under_120s": under(120),
        "pct_over_180s": over(180),
        "pct_no_move_within_window": no_move / events if events else None,
        "segment_medians": {k: median(v) for k, v in sorted(segmented.items()) if v},
        "verdict": "temporal_edge_gone" if lags and under(30) and under(30) > 0.80 else "temporal_tail_possible",
    }


def summarize_values(values: Iterable[float]) -> dict[str, float | int | None]:
    clean = [v for v in values if v is not None and math.isfinite(v)]
    return {
        "n": len(clean),
        "median": median(clean) if clean else None,
        "mean": mean(clean) if clean else None,
        "std": pstdev(clean) if len(clean) > 1 else None,
    }


def test2_spreads(rows: list[dict[str, float | str]]) -> dict[str, Any]:
    by_bucket: dict[str, list[float]] = defaultdict(list)
    roshan: list[float] = []
    baseline: list[float] = []
    late: list[float] = []
    for row in rows:
        minute = float(row["minute"])
        spread = float(row["spread"])
        bucket_start = int(min(minute, 49.999) // 5 * 5)
        bucket = f"{bucket_start:02d}-{bucket_start + 5:02d}"
        by_bucket[bucket].append(spread)
        if 17 <= minute <= 23:
            roshan.append(spread)
        if 5 <= minute <= 15:
            baseline.append(spread)
        if minute >= 35:
            late.append(spread)

    bucket_rows = []
    for bucket in sorted(by_bucket):
        s = summarize_values(by_bucket[bucket])
        bucket_rows.append({"bucket": bucket, **s})
    roshan_med = median(roshan) if roshan else None
    baseline_med = median(baseline) if baseline else None
    ratio = roshan_med / baseline_med if roshan_med is not None and baseline_med else None
    return {
        "spread_by_minute_bucket": bucket_rows,
        "roshan_window_spread": roshan_med,
        "baseline_spread": baseline_med,
        "roshan_vs_baseline_ratio": ratio,
        "late_game_spread": median(late) if late else None,
        "verdict": "no_hidden_state_spread_signal" if ratio is not None and ratio < 1.15 else "possible_hidden_state_spread_signal",
    }


def test3_r2_decomposition(rows: list[dict[str, float | str]]) -> dict[str, Any]:
    if not rows:
        return {}
    x_raw = np.array([[r["nw_diff"], r["kill_diff"], r["minute"]] for r in rows], dtype=float)
    y = np.array([r["market_mid"] for r in rows], dtype=float)
    game_ids = np.array([[str(r["match_id"])] for r in rows], dtype=object)

    raw_model = LinearRegression().fit(x_raw, y)
    r2_raw = r2_score(y, raw_model.predict(x_raw))

    poly = PolynomialFeatures(degree=3, include_bias=False)
    x_poly = poly.fit_transform(x_raw)
    poly_model = LinearRegression().fit(x_poly, y)
    r2_poly = r2_score(y, poly_model.predict(x_poly))

    encoder = OneHotEncoder(drop="first", sparse_output=False, handle_unknown="ignore")
    game_fe = encoder.fit_transform(game_ids)
    x_fe = np.hstack([x_poly, game_fe])
    fe_model = LinearRegression().fit(x_fe, y)
    r2_fe = r2_score(y, fe_model.predict(x_fe))

    return {
        "r2_raw_state_linear": r2_raw,
        "r2_raw_state_poly": r2_poly,
        "r2_with_game_fe": r2_fe,
        "r2_gap": r2_fe - r2_poly,
        "n_games": len({str(r["match_id"]) for r in rows}),
        "n_rows": len(rows),
        "verdict": "large_game_specific_component" if r2_fe - r2_poly > 0.20 else "small_game_specific_component",
    }


def first_valid_mid(path: Path) -> tuple[float, float] | None:
    if not path.exists():
        return None
    last_mid: float | None = None
    for row in read_csv(path):
        bid = fnum(row.get("yes_bid"))
        ask = fnum(row.get("yes_ask"))
        if bid is None or ask is None or ask < bid:
            continue
        mid = (bid + ask) / 2.0
        last_mid = mid
        if 0.02 < mid < 0.98:
            return mid, fnum(row.get("gt_min")) or 0.0
    return (last_mid, 0.0) if last_mid is not None else None


def mid_near_minute(path: Path, target_minute: float) -> float | None:
    best: tuple[float, float] | None = None
    if not path.exists():
        return None
    for row in read_csv(path):
        minute = fnum(row.get("gt_min"))
        bid = fnum(row.get("yes_bid"))
        ask = fnum(row.get("yes_ask"))
        if minute is None or bid is None or ask is None or ask < bid:
            continue
        dt = abs(minute - target_minute)
        mid = (bid + ask) / 2.0
        if best is None or dt < best[0]:
            best = (dt, mid)
    return best[1] if best and best[0] <= 3.0 else None


def bucket_label_price(p: float) -> str:
    edges = [(0.0, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.0)]
    for lo, hi in edges:
        if lo <= p < hi or (hi == 1.0 and p <= hi):
            return f"{lo:.1f}-{hi:.1f}"
    return "unknown"


def bucket_label_duration(duration: float) -> str:
    if duration < 30:
        return "<30"
    if duration < 40:
        return "30-40"
    if duration < 50:
        return "40-50"
    return "50+"


def test4_residuals(clean_dir: Path, match_rows: list[dict[str, str]]) -> dict[str, Any]:
    rows = []
    for match in match_rows:
        match_id = str(match["match_id"])
        path = clean_dir / "snapshots" / f"{match_id}.csv"
        first = first_valid_mid(path)
        if first is None:
            continue
        early_mid, first_minute = first
        late_mid = mid_near_minute(path, 15.0)
        outcome = float(match["yes_won"])
        duration = fnum(match.get("duration_min")) or 0.0
        rows.append(
            {
                "match_id": match_id,
                "market_mid_early": early_mid,
                "first_market_minute": first_minute,
                "market_mid_late": late_mid,
                "outcome": outcome,
                "residual": outcome - early_mid,
                "duration_min": duration,
                "mapping": match.get("mapping") or "",
            }
        )
    residuals = [r["residual"] for r in rows]
    outcomes = [r["outcome"] for r in rows]
    early_abs_errors = [abs(r["outcome"] - r["market_mid_early"]) for r in rows]
    early_briers = [(r["outcome"] - r["market_mid_early"]) ** 2 for r in rows]
    first_market_minutes = [r["first_market_minute"] for r in rows if r["first_market_minute"] is not None]

    by_duration: dict[str, list[dict[str, float]]] = defaultdict(list)
    by_price: dict[str, list[dict[str, float]]] = defaultdict(list)
    by_mapping: dict[str, list[dict[str, float]]] = defaultdict(list)
    early_late = {"early_abs_error": [], "late_abs_error": []}
    for row in rows:
        by_duration[bucket_label_duration(row["duration_min"])].append(row)
        by_price[bucket_label_price(row["market_mid_early"])].append(row)
        by_mapping[str(row["mapping"])].append(row)
        early_late["early_abs_error"].append(abs(row["outcome"] - row["market_mid_early"]))
        if row["market_mid_late"] is not None:
            early_late["late_abs_error"].append(abs(row["outcome"] - row["market_mid_late"]))

    duration_rows = []
    for bucket, vals in sorted(by_duration.items()):
        duration_rows.append(
            {
                "bucket": bucket,
                "n": len(vals),
                "residual_mean": mean(v["residual"] for v in vals),
                "actual_win_rate": mean(v["outcome"] for v in vals),
                "avg_market_mid": mean(v["market_mid_early"] for v in vals),
            }
        )

    calibration_rows = []
    for bucket, vals in sorted(by_price.items()):
        calibration_rows.append(
            {
                "bucket": bucket,
                "n": len(vals),
                "avg_market_mid": mean(v["market_mid_early"] for v in vals),
                "actual_win_rate": mean(v["outcome"] for v in vals),
                "residual_mean": mean(v["residual"] for v in vals),
            }
        )

    mapping_rows = []
    for bucket, vals in sorted(by_mapping.items()):
        mapping_rows.append(
            {
                "mapping": bucket,
                "n": len(vals),
                "actual_win_rate": mean(v["outcome"] for v in vals),
                "avg_market_mid": mean(v["market_mid_early"] for v in vals),
                "residual_mean": mean(v["residual"] for v in vals),
                "mae": mean(abs(v["residual"]) for v in vals),
            }
        )

    return {
        "n_games": len(rows),
        "outcome_mean": mean(outcomes) if outcomes else None,
        "residual_mean": mean(residuals) if residuals else None,
        "residual_std": pstdev(residuals) if len(residuals) > 1 else None,
        "early_mae": mean(early_abs_errors) if early_abs_errors else None,
        "constant_0_5_mae": 0.5 if rows else None,
        "early_brier": mean(early_briers) if early_briers else None,
        "constant_0_5_brier": 0.25 if rows else None,
        "first_market_game_minute": {
            "min": min(first_market_minutes) if first_market_minutes else None,
            "p25": pct(first_market_minutes, 25),
            "median": median(first_market_minutes) if first_market_minutes else None,
            "p75": pct(first_market_minutes, 75),
            "max": max(first_market_minutes) if first_market_minutes else None,
            "count_lt_1m": sum(v < 1 for v in first_market_minutes),
            "count_lt_5m": sum(v < 5 for v in first_market_minutes),
        },
        "residual_by_mapping": mapping_rows,
        "residual_by_duration_bucket": duration_rows,
        "calibration_by_price_bucket": calibration_rows,
        "early_vs_late_price_accuracy": {
            "early_mae": mean(early_late["early_abs_error"]) if early_late["early_abs_error"] else None,
            "late_15m_mae": mean(early_late["late_abs_error"]) if early_late["late_abs_error"] else None,
            "late_15m_n": len(early_late["late_abs_error"]),
        },
    }


def print_test3_only(result: dict[str, Any]) -> None:
    print("TEST 3: Mid vs Raw State R2 Decomposition")
    for key in ["r2_raw_state_linear", "r2_raw_state_poly", "r2_with_game_fe", "r2_gap", "n_games", "n_rows", "verdict"]:
        print(f"  {key}: {result.get(key)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean-dir", default="data/clean")
    parser.add_argument("--raw-snapshots", default="logs/raw_snapshots.csv")
    parser.add_argument("--book-events", default="logs/book_events.csv")
    parser.add_argument("--markets", default="markets.yaml")
    parser.add_argument("--market-type", default="MAP_WINNER", help="Use empty string for all market types")
    parser.add_argument("--nw-delta-min", type=float, default=300.0)
    parser.add_argument("--ask-move-min", type=float, default=0.01)
    parser.add_argument("--reaction-window-sec", type=float, default=300.0)
    parser.add_argument("--test3-only", action="store_true")
    parser.add_argument("--output-json", default="reports/market_characterization.json")
    args = parser.parse_args()

    clean_dir = Path(args.clean_dir)
    market_type = args.market_type or None
    match_rows = load_match_index(clean_dir / "matches.csv", market_type)
    clean_rows = clean_market_rows(clean_dir, match_rows)

    test3 = test3_r2_decomposition(clean_rows)
    if args.test3_only:
        print_test3_only(test3)
        return

    mappings = load_mappings(Path(args.markets), market_type)
    results = {
        "inputs": {
            "market_type": market_type,
            "clean_matches": len(match_rows),
            "clean_book_rows": len(clean_rows),
            "mapped_matches": len(mappings),
            "nw_delta_min": args.nw_delta_min,
            "ask_move_min": args.ask_move_min,
            "reaction_window_sec": args.reaction_window_sec,
        },
        "test1_reaction_speed": test1_reaction_speed(
            Path(args.raw_snapshots),
            Path(args.book_events),
            mappings,
            nw_delta_min=args.nw_delta_min,
            ask_move_min=args.ask_move_min,
            reaction_window_sec=args.reaction_window_sec,
        ),
        "test2_spread_behavior": test2_spreads(clean_rows),
        "test3_r2_decomposition": test3,
        "test4_draft_residual_proxy": test4_residuals(clean_dir, match_rows),
        "branch_decision": {
            "default": "Version B: Draft Market Residual Model",
            "reason": "No reliable pre-draft sharp bookmaker odds corpus is present locally; earliest Polymarket mid is a post-draft market anchor, not p_anchor.",
        },
    }

    out = Path(args.output_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(results, indent=2, sort_keys=True))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
