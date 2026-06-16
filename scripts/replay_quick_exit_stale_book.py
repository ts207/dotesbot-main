#!/usr/bin/env python3
"""Replay quick-exit stale-book strategy with executable ask-entry/bid-exit."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from replay_existing_signals_from_raw import (  # noqa: E402
    BOOK_COLUMNS,
    LATENCY_COLUMNS,
    SIGNAL_COLUMNS,
    SNAPSHOT_COLUMNS,
    SOURCE_DELAY_COLUMNS,
    build_index,
    causality,
    first_at_or_after,
    fnum,
    latest_before,
    load_config,
    simulate_fill,
)
from unified_storage.event_store import (  # noqa: E402
    NS_PER_SECOND,
    load_manual_windows,
    manual_window_reason,
    read_table,
    table_rows,
)


REPLAY_DECISIONS = {"paper_buy_yes", "paper_buy_no", "live_buy_yes", "live_buy_no", "buy"}

HEADERS = [
    "strategy_name",
    "event_family_mode",
    "exit_horizon_sec",
    "signal_id",
    "decision_ts",
    "decision_utc",
    "match_id",
    "market_name",
    "event_type",
    "event_family",
    "token_id",
    "side",
    "decision",
    "latest_snapshot_ts_used",
    "latest_book_ts_used",
    "latest_signal_ts_used",
    "latest_latency_ts_used",
    "latest_source_delay_ts_used",
    "causality_valid",
    "causality_violation_reason",
    "entry_best_bid",
    "entry_best_ask",
    "entry_bid_size",
    "entry_ask_size",
    "entry_spread",
    "entry_depth_usd",
    "limit_price",
    "target_size_usd",
    "filled_size_usd",
    "entry_price",
    "entry_fillable",
    "entry_fill_reason",
    "exit_target_ts",
    "exit_book_ts",
    "exit_bid",
    "exit_ask",
    "exit_bid_size",
    "exit_spread",
    "exit_liquidity_available",
    "exit_reason",
    "exit_slippage",
    "shares",
    "gross_pnl",
    "net_pnl",
    "roi",
    "source_delay_sec",
    "latency_book_age_at_signal_ms",
    "snapshot_age_sec",
    "book_age_sec",
    "manual_excluded",
]


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def event_mode_allows(mode: str, event_type: str | None) -> bool:
    event = event_type or ""
    if mode == "all":
        return True
    if mode == "POLL_LATE_FIGHT_FLIP_only":
        return event == "POLL_LATE_FIGHT_FLIP"
    if mode == "exclude_POLL_FIGHT_SWING":
        return event != "POLL_FIGHT_SWING"
    if mode.endswith("_only"):
        return event == mode.removesuffix("_only")
    if mode.startswith("exclude_"):
        return event != mode.removeprefix("exclude_")
    return False


def find_exit_bid(
    book_by_token: dict[str, list[dict]],
    token_id: str,
    target_ns: int,
    timeout_seconds: int,
) -> tuple[dict | None, str]:
    rows = book_by_token.get(str(token_id), [])
    if not rows:
        return None, "no_book_rows"
    first = first_at_or_after(book_by_token, token_id, target_ns)
    if first and fnum(first.get("best_bid")) is not None:
        return first, "target_bid_available"
    end_ns = target_ns + timeout_seconds * NS_PER_SECOND
    for row in rows:
        ts = row.get("received_at_ns")
        if ts is None or ts < target_ns:
            continue
        if ts > end_ns:
            break
        if fnum(row.get("best_bid")) is not None:
            return row, "next_bid_within_timeout"
    return first, "no_exit_liquidity"


def depth_usd(price: float | None, size: float | None) -> float | None:
    if price is None or size is None:
        return None
    return price * size


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow({h: row.get(h) for h in HEADERS})


def summarize(rows: list[dict], signals_total: int, excluded_signals: int) -> dict:
    by_mode_horizon = {}
    for (mode, horizon), grouped in sorted(
        group_by(rows, ["event_family_mode", "exit_horizon_sec"]).items(),
        key=lambda item: (item[0][0], int(item[0][1])),
    ):
        fills = [r for r in grouped if str(r.get("entry_fillable")) == "True"]
        exits = [r for r in fills if str(r.get("exit_liquidity_available")) == "True"]
        pnl = [fnum(r.get("net_pnl")) for r in exits]
        pnl = [p for p in pnl if p is not None]
        key = f"{mode}_{horizon}s"
        by_mode_horizon[key] = {
            "event_family_mode": mode,
            "exit_horizon_sec": int(horizon),
            "signals": len(grouped),
            "fills": len(fills),
            "fill_rate": len(fills) / len(grouped) if grouped else 0.0,
            "exits": len(exits),
            "exit_liquidity_rate": len(exits) / len(fills) if fills else 0.0,
            "net_pnl": sum(pnl) if pnl else None,
            "profit_per_signal": (sum(pnl) / len(grouped)) if grouped and pnl else None,
            "profit_per_fill": (sum(pnl) / len(fills)) if fills and pnl else None,
        }
    violations = [r for r in rows if str(r.get("causality_valid")) != "True"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy_name": "quick_exit_stale_book_v1",
        "entry_source": "existing_signals",
        "fill_model": "conservative_trade_through",
        "exit_model": "ask_entry_bid_exit",
        "signals_total": signals_total,
        "manual_windows_excluded": True,
        "excluded_events_count": excluded_signals,
        "causality_violations": len(violations),
        "causality_violation_examples": violations[:10],
        "by_mode_horizon": by_mode_horizon,
    }


def group_by(rows: list[dict], fields: list[str]) -> dict[tuple, list[dict]]:
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[tuple(str(row.get(f) or "") for f in fields)].append(row)
    return grouped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/quick_exit_stale_book_v1.yaml", type=Path)
    args = parser.parse_args()

    cfg = load_yaml(args.config)
    paths = cfg.get("paths", {})
    data_root = Path(paths.get("data_root", "data_v2"))
    quarantine_manifest = Path(paths.get("quarantine_manifest", "data_v2/quarantine_manifest.csv"))
    manual_windows_path = Path(paths.get("manual_excluded_windows", "data/manual/excluded_time_windows.csv"))
    fill_cfg = load_config(Path(paths.get("fill_model_config", "configs/reaction_lag_fill_model_v1.yaml")))
    fill_model = str(cfg.get("fill_model") or "conservative_trade_through")
    horizons = [int(x) for x in cfg.get("fixed_exit_horizons_seconds", [15, 30, 45, 60])]
    modes = [str(x) for x in cfg.get("event_family_modes", ["all"])]
    exit_timeout = int(cfg.get("exit_bid_timeout_seconds") or 0)
    max_spread = fnum(cfg.get("max_spread"))
    min_depth = fnum(cfg.get("min_depth_usd"))

    signals = table_rows(read_table(data_root / "signals", quarantine_manifest, SIGNAL_COLUMNS, repo_root=REPO_ROOT))
    books = table_rows(read_table(data_root / "book_ticks", quarantine_manifest, BOOK_COLUMNS, repo_root=REPO_ROOT))
    snapshots = table_rows(read_table(data_root / "snapshots", quarantine_manifest, SNAPSHOT_COLUMNS, repo_root=REPO_ROOT))
    source_delay = table_rows(read_table(data_root / "source_delay", quarantine_manifest, SOURCE_DELAY_COLUMNS, repo_root=REPO_ROOT))
    latency = table_rows(read_table(data_root / "latency", quarantine_manifest, LATENCY_COLUMNS, repo_root=REPO_ROOT))

    book_by_token = build_index(books, "asset_id")
    snapshot_by_match = build_index(snapshots, "match_id")
    source_by_match = build_index(source_delay, "match_id")
    latency_by_match = build_index(latency, "match_id")
    manual_windows = load_manual_windows(manual_windows_path)

    rows: list[dict] = []
    excluded_signals = 0
    for sig in signals:
        decision_ns = sig.get("received_at_ns")
        if manual_window_reason(decision_ns, manual_windows):
            excluded_signals += 1
            continue
        if str(sig.get("decision")) not in REPLAY_DECISIONS:
            continue
        token_id = str(sig.get("token_id") or "")
        if not token_id or decision_ns is None:
            continue

        entry_book = latest_before(book_by_token, token_id, decision_ns)
        entry_ask = fnum((entry_book or {}).get("best_ask"))
        entry_spread = fnum((entry_book or {}).get("spread"))
        entry_ask_size = fnum((entry_book or {}).get("ask_size"))
        entry_depth = depth_usd(entry_ask, entry_ask_size)
        if entry_ask is None:
            continue
        if max_spread is not None and entry_spread is not None and entry_spread > max_spread:
            continue
        if min_depth is not None and entry_depth is not None and entry_depth < min_depth:
            continue

        latest_snapshot = latest_before(snapshot_by_match, sig.get("match_id"), decision_ns)
        latest_source = latest_before(source_by_match, sig.get("match_id"), decision_ns)
        latest_latency = latest_before(latency_by_match, sig.get("match_id"), decision_ns)
        stamps = {
            "latest_snapshot_ts_used": (latest_snapshot or {}).get("received_at_ns"),
            "latest_book_ts_used": (entry_book or {}).get("received_at_ns"),
            "latest_signal_ts_used": decision_ns,
            "latest_latency_ts_used": (latest_latency or {}).get("received_at_ns"),
            "latest_source_delay_ts_used": (latest_source or {}).get("received_at_ns"),
        }
        causal_ok, violation = causality(decision_ns, stamps)

        fill = simulate_fill(fill_model, entry_book, sig, book_by_token, fill_cfg)
        entry_price = fnum(fill.get("fill_price"))
        filled_usd = fnum(fill.get("filled_size_usd")) or 0.0
        shares = filled_usd / entry_price if entry_price and entry_price > 0 else 0.0

        for mode in modes:
            if not event_mode_allows(mode, sig.get("event_type")):
                continue
            for horizon in horizons:
                target_ns = int(decision_ns) + horizon * NS_PER_SECOND
                exit_book, exit_reason = find_exit_bid(book_by_token, token_id, target_ns, exit_timeout)
                exit_bid = fnum((exit_book or {}).get("best_bid"))
                exit_ask = fnum((exit_book or {}).get("best_ask"))
                exit_available = bool(fill.get("fillable")) and exit_bid is not None
                gross_pnl = shares * exit_bid - filled_usd if exit_available else None
                net_pnl = gross_pnl
                row = {
                    "strategy_name": cfg.get("strategy_name", "quick_exit_stale_book_v1"),
                    "event_family_mode": mode,
                    "exit_horizon_sec": horizon,
                    "signal_id": sig.get("signal_id"),
                    "decision_ts": decision_ns,
                    "decision_utc": sig.get("received_at_utc"),
                    "match_id": sig.get("match_id"),
                    "market_name": sig.get("market_name"),
                    "event_type": sig.get("event_type"),
                    "event_family": sig.get("event_family"),
                    "token_id": token_id,
                    "side": sig.get("side"),
                    "decision": sig.get("decision"),
                    "latest_snapshot_ts_used": stamps["latest_snapshot_ts_used"],
                    "latest_book_ts_used": stamps["latest_book_ts_used"],
                    "latest_signal_ts_used": stamps["latest_signal_ts_used"],
                    "latest_latency_ts_used": stamps["latest_latency_ts_used"],
                    "latest_source_delay_ts_used": stamps["latest_source_delay_ts_used"],
                    "causality_valid": causal_ok,
                    "causality_violation_reason": violation,
                    "entry_best_bid": fnum((entry_book or {}).get("best_bid")),
                    "entry_best_ask": entry_ask,
                    "entry_bid_size": fnum((entry_book or {}).get("bid_size")),
                    "entry_ask_size": entry_ask_size,
                    "entry_spread": entry_spread,
                    "entry_depth_usd": entry_depth,
                    "limit_price": fill.get("limit_price"),
                    "target_size_usd": fnum(sig.get("target_size_usd")) or fnum(fill_cfg.get("max_order_size_usd")),
                    "filled_size_usd": filled_usd,
                    "entry_price": entry_price,
                    "entry_fillable": fill.get("fillable"),
                    "entry_fill_reason": fill.get("fill_reason"),
                    "exit_target_ts": target_ns,
                    "exit_book_ts": (exit_book or {}).get("received_at_ns"),
                    "exit_bid": exit_bid,
                    "exit_ask": exit_ask,
                    "exit_bid_size": fnum((exit_book or {}).get("bid_size")),
                    "exit_spread": fnum((exit_book or {}).get("spread")),
                    "exit_liquidity_available": exit_available,
                    "exit_reason": exit_reason if fill.get("fillable") else "no_entry_fill",
                    "exit_slippage": (exit_ask - exit_bid) if exit_available and exit_ask is not None else None,
                    "shares": shares,
                    "gross_pnl": gross_pnl,
                    "net_pnl": net_pnl,
                    "roi": (net_pnl / filled_usd) if net_pnl is not None and filled_usd else None,
                    "source_delay_sec": fnum((latest_source or {}).get("game_time_lag_sec")),
                    "latency_book_age_at_signal_ms": fnum((latest_latency or {}).get("book_age_at_signal_ms")),
                    "snapshot_age_sec": ((decision_ns - stamps["latest_snapshot_ts_used"]) / NS_PER_SECOND)
                    if stamps["latest_snapshot_ts_used"] is not None
                    else None,
                    "book_age_sec": ((decision_ns - stamps["latest_book_ts_used"]) / NS_PER_SECOND)
                    if stamps["latest_book_ts_used"] is not None
                    else None,
                    "manual_excluded": False,
                }
                rows.append(row)

    replay_path = Path(paths.get("replay_csv", "reports/quick_exit_stale_book_replay.csv"))
    report_path = Path(paths.get("report_json", "reports/quick_exit_stale_book_report.json"))
    write_csv(replay_path, rows)
    report = summarize(rows, len(signals), excluded_signals)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"wrote {replay_path}")
    print(f"wrote {report_path}")
    print(
        json.dumps(
            {
                "rows": len(rows),
                "causality_violations": report["causality_violations"],
                "modes": modes,
                "horizons": horizons,
            },
            sort_keys=True,
        )
    )
    return 2 if report["causality_violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
