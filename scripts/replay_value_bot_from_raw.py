#!/usr/bin/env python3
"""Replay Value bot from raw data_v2 snapshots/book ticks and write audit reports."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.backtest_value_engine import (  # noqa: E402
    _params,
    load_books,
    load_markets,
    load_outcomes,
    load_snapshots,
    replay,
)
from unified_storage.event_store import load_manual_windows, manual_window_reason  # noqa: E402


HEADERS = [
    "variant",
    "decision_ts",
    "decision_utc",
    "latest_snapshot_ts_used",
    "latest_book_ts_used",
    "causality_valid",
    "causality_violation_reason",
    "manual_excluded",
    "match_id",
    "market_name",
    "token_id",
    "side",
    "entry_price",
    "entry_price_source",
    "fair",
    "edge",
    "lead",
    "game_time_sec",
    "book_age_ms",
    "orientation_valid",
    "won",
    "stake_usd",
    "pnl_usd",
    "outcome_source",
]


def fnum(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow({h: row.get(h) for h in HEADERS})


def top_share(values: list[float], n: int) -> float | None:
    if not values:
        return None
    denom = sum(abs(v) for v in values)
    if denom == 0:
        return None
    return sum(abs(v) for v in sorted(values, key=lambda x: abs(x), reverse=True)[:n]) / denom


def normalize_trade(raw: dict, variant: str, manual_windows) -> dict:
    decision_ts = raw.get("decision_ts")
    book_ts = raw.get("entry_book_ts")
    violations = []
    if decision_ts is None:
        violations.append("missing_decision_ts")
    if book_ts is None:
        violations.append("missing_book_ts")
    if decision_ts is not None and book_ts is not None and int(book_ts) > int(decision_ts):
        violations.append("book_ts_after_decision_ts")
    manual_reason = manual_window_reason(int(decision_ts) if decision_ts is not None else None, manual_windows)
    lead = int(raw.get("lead") or 0)
    ask = fnum(raw.get("ask"))
    orientation_valid = not (abs(lead) > int(_params()["flip_lead"]) and ask is not None and ask < float(_params()["flip_ask_floor"]))
    return {
        "variant": variant,
        "decision_ts": decision_ts,
        "decision_utc": raw.get("decision_utc"),
        "latest_snapshot_ts_used": decision_ts,
        "latest_book_ts_used": book_ts,
        "causality_valid": not violations,
        "causality_violation_reason": ";".join(violations),
        "manual_excluded": manual_reason is not None,
        "match_id": raw.get("match_id"),
        "market_name": raw.get("name"),
        "token_id": raw.get("token_id"),
        "side": raw.get("side"),
        "entry_price": raw.get("ask"),
        "entry_price_source": "actual_best_ask",
        "fair": raw.get("fair"),
        "edge": raw.get("edge"),
        "lead": raw.get("lead"),
        "game_time_sec": raw.get("game_time"),
        "book_age_ms": raw.get("book_age_ms"),
        "orientation_valid": orientation_valid,
        "won": raw.get("won"),
        "stake_usd": raw.get("stake"),
        "pnl_usd": raw.get("pnl"),
        "outcome_source": raw.get("outcome_source"),
    }


def summarize_variant(rows: list[dict], raw_signals: int, rejects: Counter, coverage: Counter, unresolved: list) -> dict:
    included = [r for r in rows if not r.get("manual_excluded")]
    wins = sum(1 for r in included if int(r.get("won") or 0) == 1)
    pnl = sum(fnum(r.get("pnl_usd")) or 0.0 for r in included)
    stake = sum(fnum(r.get("stake_usd")) or 0.0 for r in included)
    pnls = [fnum(r.get("pnl_usd")) for r in included]
    pnls = [p for p in pnls if p is not None]
    by_match: dict[str, float] = defaultdict(float)
    for row in included:
        by_match[str(row.get("match_id") or "")] += fnum(row.get("pnl_usd")) or 0.0
    return {
        "raw_signals": raw_signals,
        "trades": len(included),
        "manual_excluded_trades": len(rows) - len(included),
        "wins": wins,
        "losses": len(included) - wins,
        "win_rate": wins / len(included) if included else None,
        "pnl_usd": pnl,
        "stake_usd": stake,
        "roi": pnl / stake if stake else None,
        "avg_pnl_usd": pnl / len(included) if included else None,
        "causality_violations": sum(1 for r in included if not r.get("causality_valid")),
        "orientation_invalid": sum(1 for r in included if not r.get("orientation_valid")),
        "entry_price_source": "actual_best_ask",
        "manual_windows_excluded": True,
        "coverage_sources": dict(coverage),
        "unresolved_matches": len(unresolved),
        "top_rejects": rejects.most_common(20),
        "top_1_trade_pnl_share": top_share(pnls, 1),
        "top_3_trade_pnl_share": top_share(pnls, 3),
        "top_5_trade_pnl_share": top_share(pnls, 5),
        "top_1_match_pnl_share": top_share(list(by_match.values()), 1),
        "top_3_match_pnl_share": top_share(list(by_match.values()), 3),
        "pnl_by_match_id": dict(sorted(by_match.items(), key=lambda item: abs(item[1]), reverse=True)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manual-windows", default="data/manual/excluded_time_windows.csv", type=Path)
    parser.add_argument("--trades-output", default="reports/value_bot_raw_replay_trades.csv", type=Path)
    parser.add_argument("--report-output", default="reports/value_bot_raw_replay_report.json", type=Path)
    args = parser.parse_args()

    params = _params()
    outcomes, outcome_sources = load_outcomes()
    markets, skipped = load_markets()
    snapshots = load_snapshots(set(markets))
    tokens: set[str] = set()
    for match_id in snapshots:
        tokens.add(str(markets[match_id]["yes_token_id"]))
        tokens.add(str(markets[match_id]["no_token_id"]))
    book = load_books(tokens)
    joined = {
        match_id: rows
        for match_id, rows in snapshots.items()
        if str(markets[match_id]["yes_token_id"]) in book
        and str(markets[match_id]["no_token_id"]) in book
    }
    manual_windows = load_manual_windows(args.manual_windows)

    all_rows: list[dict] = []
    variant_reports = {}
    for variant, confirm in [("no_confirmation", False), ("with_confirmation", True)]:
        trades, coverage, unresolved, raw_signals, rejects = replay(
            snapshots=joined,
            markets=markets,
            book=book,
            outcomes=outcomes,
            outcome_sources=outcome_sources,
            params=params,
            confirm=confirm,
        )
        rows = [normalize_trade(trade, variant, manual_windows) for trade in trades]
        all_rows.extend(rows)
        variant_reports[variant] = summarize_variant(rows, raw_signals, rejects, coverage, unresolved)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy": "value_bot",
        "status": "raw_replay_completed",
        "params": params,
        "coverage": {
            "outcome_labels": len(outcomes),
            "valid_markets": len(markets),
            "skipped_markets": dict(skipped),
            "snapshot_matches": len(snapshots),
            "joined_matches": len(joined),
            "snapshot_rows": sum(len(v) for v in joined.values()),
            "book_ticks": sum(len(v[0]) for v in book.values()),
        },
        "manual_windows_excluded": True,
        "variants": variant_reports,
        "pass_conditions": {
            "causality_violations": sum(v["causality_violations"] for v in variant_reports.values()),
            "orientation_invalid": sum(v["orientation_invalid"] for v in variant_reports.values()),
            "entry_at_actual_ask": True,
        },
    }

    write_csv(args.trades_output, all_rows)
    args.report_output.parent.mkdir(parents=True, exist_ok=True)
    args.report_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.trades_output}")
    print(f"wrote {args.report_output}")
    print(
        json.dumps(
            {
                "no_confirmation_trades": variant_reports["no_confirmation"]["trades"],
                "with_confirmation_trades": variant_reports["with_confirmation"]["trades"],
                "causality_violations": report["pass_conditions"]["causality_violations"],
                "orientation_invalid": report["pass_conditions"]["orientation_invalid"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["pass_conditions"]["causality_violations"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
