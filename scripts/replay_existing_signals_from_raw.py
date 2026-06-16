#!/usr/bin/env python3
"""Replay existing data_v2 signal decisions from raw event-time data."""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from unified_storage.event_store import (
    NS_PER_SECOND,
    load_manual_windows,
    manual_window_reason,
    read_table,
    table_rows,
)


SIGNAL_COLUMNS = [
    "signal_id",
    "received_at_utc",
    "received_at_ns",
    "match_id",
    "market_name",
    "event_type",
    "event_family",
    "event_direction",
    "token_id",
    "side",
    "decision",
    "skip_reason",
    "executable_price",
    "target_size_usd",
    "fair_price",
    "ask",
    "bid",
    "spread",
    "ask_size",
    "book_age_at_signal_ms",
    "source_update_age_sec",
    "steam_age_ms",
]
BOOK_COLUMNS = ["asset_id", "received_at_ns", "best_bid", "best_ask", "bid_size", "ask_size", "mid", "spread"]
SNAPSHOT_COLUMNS = ["match_id", "received_at_ns", "game_time_sec", "radiant_lead", "data_source"]
SOURCE_DELAY_COLUMNS = ["match_id", "received_at_ns", "game_time_lag_sec", "realtime_stats_age_sec"]
LATENCY_COLUMNS = [
    "match_id",
    "token_id",
    "received_at_ns",
    "book_received_at_ns",
    "book_age_at_signal_ms",
    "steam_received_at_ns",
    "steam_source_update_age_sec",
]
TRADE_ATTEMPT_COLUMNS = ["received_at_ns"]

TRADE_HEADERS = [
    "signal_id",
    "decision_ts",
    "decision_utc",
    "match_id",
    "market_name",
    "event_type",
    "event_family",
    "direction_mode",
    "token_id",
    "side",
    "decision",
    "fill_assumption",
    "latest_snapshot_ts_used",
    "latest_book_ts_used",
    "latest_signal_ts_used",
    "latest_latency_ts_used",
    "latest_source_delay_ts_used",
    "causality_valid",
    "causality_violation_reason",
    "manual_excluded",
    "raw_best_bid",
    "raw_best_ask",
    "raw_bid_size",
    "raw_ask_size",
    "raw_spread",
    "fair_price",
    "signal_strength",
    "initial_staleness",
    "limit_price",
    "target_size_usd",
    "filled_size_usd",
    "fill_price",
    "fillable",
    "fill_reason",
    "fill_slippage",
    "spread_paid",
    "source_delay_sec",
    "latency_book_age_at_signal_ms",
    "snapshot_age_sec",
    "book_age_sec",
    "pnl_to_settlement",
]


def fnum(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(x):
        return None
    return x


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_index(rows: list[dict], key: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        value = row.get(key)
        ts = row.get("received_at_ns")
        if value is None or ts is None:
            continue
        out[str(value)].append(row)
    for grouped in out.values():
        grouped.sort(key=lambda r: r["received_at_ns"])
    return out


def latest_before(index: dict[str, list[dict]], key: str | None, decision_ns: int | None) -> dict | None:
    if key is None or decision_ns is None:
        return None
    rows = index.get(str(key))
    if not rows:
        return None
    times = [r["received_at_ns"] for r in rows]
    i = bisect.bisect_right(times, decision_ns) - 1
    if i < 0:
        return None
    return rows[i]


def first_at_or_after(index: dict[str, list[dict]], key: str | None, target_ns: int | None) -> dict | None:
    if key is None or target_ns is None:
        return None
    rows = index.get(str(key))
    if not rows:
        return None
    times = [r["received_at_ns"] for r in rows]
    i = bisect.bisect_left(times, target_ns)
    if i >= len(rows):
        return None
    return rows[i]


def book_trade_through(
    book_index: dict[str, list[dict]],
    token_id: str,
    decision_ns: int,
    limit_price: float,
    timeout_seconds: int,
) -> bool:
    rows = book_index.get(str(token_id), [])
    if not rows:
        return False
    times = [r["received_at_ns"] for r in rows]
    start = bisect.bisect_right(times, decision_ns)
    end_ns = decision_ns + timeout_seconds * NS_PER_SECOND
    for row in rows[start:]:
        ts = row.get("received_at_ns")
        if ts is None or ts > end_ns:
            break
        ask = fnum(row.get("best_ask"))
        if ask is not None and ask > limit_price:
            return True
    return False


def displayed_capacity_usd(ask: float | None, ask_size: float | None) -> float:
    if ask is None or ask_size is None or ask <= 0 or ask_size <= 0:
        return 0.0
    return ask * ask_size


def simulate_fill(
    assumption: str,
    entry_book: dict | None,
    signal: dict,
    book_index: dict[str, list[dict]],
    fill_cfg: dict,
) -> dict[str, Any]:
    ask = fnum((entry_book or {}).get("best_ask"))
    signal_limit = fnum(signal.get("executable_price")) or fnum(signal.get("ask"))
    limit_price = signal_limit if signal_limit is not None else ask
    if ask is None or limit_price is None:
        return {
            "limit_price": limit_price,
            "filled_size_usd": 0.0,
            "fill_price": None,
            "fillable": False,
            "fill_reason": "missing_best_ask",
        }
    if ask > limit_price:
        return {
            "limit_price": limit_price,
            "filled_size_usd": 0.0,
            "fill_price": None,
            "fillable": False,
            "fill_reason": "ask_above_limit",
        }

    max_order = fnum(fill_cfg.get("max_order_size_usd")) or 25.0
    target_size = fnum(signal.get("target_size_usd")) or max_order
    ask_size = fnum((entry_book or {}).get("ask_size"))
    capacity = displayed_capacity_usd(ask, ask_size)
    size = min(max_order, target_size, capacity if capacity > 0 else 0.0)
    if size <= 0:
        return {
            "limit_price": limit_price,
            "filled_size_usd": 0.0,
            "fill_price": None,
            "fillable": False,
            "fill_reason": "no_displayed_ask_liquidity",
        }

    if assumption == "conservative_trade_through":
        token_id = str(signal.get("token_id") or "")
        decision_ns = int(signal["received_at_ns"])
        timeout = int(fill_cfg.get("order_timeout_seconds") or 5)
        if not book_trade_through(book_index, token_id, decision_ns, ask, timeout):
            return {
                "limit_price": limit_price,
                "filled_size_usd": 0.0,
                "fill_price": None,
                "fillable": False,
                "fill_reason": "no_trade_through_within_timeout",
            }

    return {
        "limit_price": limit_price,
        "filled_size_usd": size,
        "fill_price": ask,
        "fillable": True,
        "fill_reason": "filled",
    }


def causality(decision_ns: int | None, stamps: dict[str, int | None]) -> tuple[bool, str]:
    if decision_ns is None:
        return False, "missing_decision_ts"
    violations = []
    for name, ts in stamps.items():
        if ts is not None and ts > decision_ns:
            violations.append(f"{name}>{decision_ns}")
    return not violations, ";".join(violations)


def summarize(
    rows: list[dict],
    all_signals: list[dict],
    excluded_signals: int,
    excluded_attempts: int,
    horizons: list[int],
) -> dict:
    by_assumption: dict[str, dict] = {}
    for assumption in sorted({r["fill_assumption"] for r in rows}):
        subset = [r for r in rows if r["fill_assumption"] == assumption]
        filled = [r for r in subset if str(r.get("fillable")) == "True"]
        horizon_summary = {}
        for h in horizons:
            pnls = [fnum(r.get(f"pnl_to_{h}s_markout")) for r in filled]
            pnls = [x for x in pnls if x is not None]
            horizon_summary[f"{h}s"] = {
                "pnl": sum(pnls) if pnls else None,
                "positive_count": sum(1 for p in pnls if p > 0),
                "observations": len(pnls),
            }
        match_pnl = Counter()
        for r in filled:
            p = fnum(r.get("pnl_to_300s_markout"))
            if p is not None:
                match_pnl[str(r.get("match_id") or "")] += p
        total_abs = sum(abs(v) for v in match_pnl.values())
        top_abs_share = max((abs(v) for v in match_pnl.values()), default=0.0) / total_abs if total_abs else None
        by_assumption[assumption] = {
            "signals_replayed": len(subset),
            "filled_count": len(filled),
            "fill_rate": len(filled) / len(subset) if subset else 0.0,
            "markout_horizons": horizon_summary,
            "pnl_to_60s_markout": horizon_summary.get("60s", {}).get("pnl"),
            "pnl_to_300s_markout": horizon_summary.get("300s", {}).get("pnl"),
            "positive_60s_markout_count": horizon_summary.get("60s", {}).get("positive_count", 0),
            "positive_300s_markout_count": horizon_summary.get("300s", {}).get("positive_count", 0),
            "top_match_abs_pnl_share_300s": top_abs_share,
        }

    decision_counts = Counter(str(r.get("decision")) for r in all_signals)
    violations = [r for r in rows if str(r.get("causality_valid")) != "True"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "direction_mode": "existing_signal_tradable",
        "signal_rows_total": len(all_signals),
        "decision_counts": dict(decision_counts),
        "manual_windows_excluded": True,
        "excluded_events_count": excluded_signals,
        "excluded_trade_attempts_count": excluded_attempts,
        "causality_violations": len(violations),
        "causality_violation_examples": violations[:10],
        "by_fill_assumption": by_assumption,
    }


def trade_headers(horizons: list[int]) -> list[str]:
    headers = list(TRADE_HEADERS)
    insert_at = headers.index("pnl_to_settlement")
    dynamic = []
    for h in horizons:
        dynamic.extend([f"markout_mid_{h}s", f"entry_vs_mid_{h}s", f"pnl_to_{h}s_markout"])
    headers[insert_at:insert_at] = dynamic
    return headers


def write_trades(path: Path, rows: list[dict], horizons: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = trade_headers(horizons)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({h: row.get(h) for h in headers})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/reaction_lag_replay_v1.yaml", type=Path)
    args = parser.parse_args()

    cfg = load_config(args.config)
    paths = cfg.get("paths", {})
    data_root = Path(paths.get("data_root", "data_v2"))
    quarantine_manifest = Path(paths.get("quarantine_manifest", "data_v2/quarantine_manifest.csv"))
    manual_windows_path = Path(paths.get("manual_excluded_windows", "data/manual/excluded_time_windows.csv"))
    fill_cfg = load_config(Path(paths.get("fill_model", "configs/reaction_lag_fill_model_v1.yaml")))
    fill_assumptions = sorted((fill_cfg.get("fill_assumptions") or {}).keys())
    if not fill_assumptions:
        fill_assumptions = ["optimistic_displayed_liquidity", "conservative_trade_through"]

    filters = cfg.get("filters", {})
    replay_decisions = {str(x) for x in filters.get("replay_decisions", [])}
    horizons = [int(x) for x in cfg.get("markouts", {}).get("horizons_sec", [60, 300])]

    signals = table_rows(read_table(data_root / "signals", quarantine_manifest, SIGNAL_COLUMNS, repo_root=REPO_ROOT))
    books = table_rows(read_table(data_root / "book_ticks", quarantine_manifest, BOOK_COLUMNS, repo_root=REPO_ROOT))
    snapshots = table_rows(read_table(data_root / "snapshots", quarantine_manifest, SNAPSHOT_COLUMNS, repo_root=REPO_ROOT))
    source_delay = table_rows(read_table(data_root / "source_delay", quarantine_manifest, SOURCE_DELAY_COLUMNS, repo_root=REPO_ROOT))
    latency = table_rows(read_table(data_root / "latency", quarantine_manifest, LATENCY_COLUMNS, repo_root=REPO_ROOT))
    attempts = table_rows(read_table(data_root / "trade_attempts", quarantine_manifest, TRADE_ATTEMPT_COLUMNS, repo_root=REPO_ROOT))

    book_by_token = build_index(books, "asset_id")
    snapshot_by_match = build_index(snapshots, "match_id")
    source_by_match = build_index(source_delay, "match_id")
    latency_by_match = build_index(latency, "match_id")
    manual_windows = load_manual_windows(manual_windows_path)

    excluded_signals = 0
    rows: list[dict] = []
    for sig in signals:
        decision_ns = sig.get("received_at_ns")
        if manual_window_reason(decision_ns, manual_windows):
            excluded_signals += 1
            continue
        if replay_decisions and str(sig.get("decision")) not in replay_decisions:
            continue
        token_id = str(sig.get("token_id") or "")
        if filters.get("require_token_id", True) and not token_id:
            continue

        entry_book = latest_before(book_by_token, token_id, decision_ns)
        if filters.get("require_best_ask", True) and fnum((entry_book or {}).get("best_ask")) is None:
            continue

        latest_snapshot = latest_before(snapshot_by_match, sig.get("match_id"), decision_ns)
        snapshot_ts = (latest_snapshot or {}).get("received_at_ns")
        latest_source = latest_before(source_by_match, sig.get("match_id"), decision_ns)
        latest_latency = latest_before(latency_by_match, sig.get("match_id"), decision_ns)
        stamps = {
            "latest_snapshot_ts_used": snapshot_ts,
            "latest_book_ts_used": (entry_book or {}).get("received_at_ns"),
            "latest_signal_ts_used": decision_ns,
            "latest_latency_ts_used": (latest_latency or {}).get("received_at_ns"),
            "latest_source_delay_ts_used": (latest_source or {}).get("received_at_ns"),
        }
        causal_ok, violation = causality(decision_ns, stamps)

        markout_rows = {}
        for h in horizons:
            row = first_at_or_after(book_by_token, token_id, decision_ns + h * NS_PER_SECOND)
            mid = fnum((row or {}).get("mid"))
            if mid is None:
                bid = fnum((row or {}).get("best_bid"))
                ask = fnum((row or {}).get("best_ask"))
                mid = (bid + ask) / 2.0 if bid is not None and ask is not None else None
            markout_rows[h] = mid

        for assumption in fill_assumptions:
            fill = simulate_fill(assumption, entry_book, sig, book_by_token, fill_cfg)
            entry_px = fnum(fill.get("fill_price"))
            filled_usd = fnum(fill.get("filled_size_usd")) or 0.0
            shares = filled_usd / entry_px if entry_px and entry_px > 0 else 0.0
            row = {
                "signal_id": sig.get("signal_id"),
                "decision_ts": decision_ns,
                "decision_utc": sig.get("received_at_utc"),
                "match_id": sig.get("match_id"),
                "market_name": sig.get("market_name"),
                "event_type": sig.get("event_type"),
                "event_family": sig.get("event_family"),
                "direction_mode": "existing_signal_tradable",
                "token_id": token_id,
                "side": sig.get("side"),
                "decision": sig.get("decision"),
                "fill_assumption": assumption,
                "latest_snapshot_ts_used": stamps["latest_snapshot_ts_used"],
                "latest_book_ts_used": stamps["latest_book_ts_used"],
                "latest_signal_ts_used": stamps["latest_signal_ts_used"],
                "latest_latency_ts_used": stamps["latest_latency_ts_used"],
                "latest_source_delay_ts_used": stamps["latest_source_delay_ts_used"],
                "causality_valid": causal_ok,
                "causality_violation_reason": violation,
                "manual_excluded": False,
                "raw_best_bid": fnum((entry_book or {}).get("best_bid")),
                "raw_best_ask": fnum((entry_book or {}).get("best_ask")),
                "raw_bid_size": fnum((entry_book or {}).get("bid_size")),
                "raw_ask_size": fnum((entry_book or {}).get("ask_size")),
                "raw_spread": fnum((entry_book or {}).get("spread")),
                "fair_price": fnum(sig.get("fair_price")),
                "signal_strength": (fnum(sig.get("fair_price")) - fnum((entry_book or {}).get("best_ask")))
                if fnum(sig.get("fair_price")) is not None and fnum((entry_book or {}).get("best_ask")) is not None
                else None,
                "initial_staleness": (fnum(fill.get("limit_price")) - fnum((entry_book or {}).get("best_ask")))
                if fnum(fill.get("limit_price")) is not None and fnum((entry_book or {}).get("best_ask")) is not None
                else None,
                "limit_price": fill.get("limit_price"),
                "target_size_usd": fnum(sig.get("target_size_usd")) or fnum(fill_cfg.get("max_order_size_usd")),
                "filled_size_usd": filled_usd,
                "fill_price": entry_px,
                "fillable": fill.get("fillable"),
                "fill_reason": fill.get("fill_reason"),
                "fill_slippage": (entry_px - fnum(sig.get("executable_price"))) if entry_px is not None and fnum(sig.get("executable_price")) is not None else None,
                "spread_paid": fnum((entry_book or {}).get("spread")),
                "source_delay_sec": fnum((latest_source or {}).get("game_time_lag_sec")),
                "latency_book_age_at_signal_ms": fnum((latest_latency or {}).get("book_age_at_signal_ms")),
                "snapshot_age_sec": ((decision_ns - snapshot_ts) / NS_PER_SECOND)
                if decision_ns is not None and snapshot_ts is not None
                else None,
                "book_age_sec": ((decision_ns - (entry_book or {}).get("received_at_ns")) / NS_PER_SECOND)
                if decision_ns is not None and (entry_book or {}).get("received_at_ns") is not None
                else None,
                "pnl_to_settlement": None,
            }
            for h in horizons:
                mid = markout_rows.get(h)
                row[f"markout_mid_{h}s"] = mid
                row[f"entry_vs_mid_{h}s"] = (mid - entry_px) if mid is not None and entry_px is not None else None
                row[f"pnl_to_{h}s_markout"] = shares * (mid - entry_px) if mid is not None and entry_px is not None else None
            rows.append(row)

    excluded_attempts = sum(1 for r in attempts if manual_window_reason(r.get("received_at_ns"), manual_windows))
    report = summarize(rows, signals, excluded_signals, excluded_attempts, horizons)

    trades_path = Path(paths.get("trades_csv", "reports/replay_existing_signals_trades.csv"))
    report_path = Path(paths.get("report_json", "reports/replay_existing_signals_report.json"))
    write_trades(trades_path, rows, horizons)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"wrote {trades_path}")
    print(f"wrote {report_path}")
    print(json.dumps({k: report[k] for k in ["signal_rows_total", "excluded_events_count", "causality_violations"]}, sort_keys=True))
    return 2 if report["causality_violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
