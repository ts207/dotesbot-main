#!/usr/bin/env python3
"""Verify the currently running paper bot.

This script is intentionally read-only for runtime logs/state. The trade-path
exercises are synthetic and in-process, so they do not add rows to logs or
submit anything to Polymarket.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import datetime
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from config import ENABLE_REAL_LIVE_TRADING, LIVE_TRADING, PAPER_TRADE_SIZE_USD
from mapping import load_valid_mappings
from market_scope import is_active_strategy_mapping
from paper_trader import PaperTrader
from poly_ws import BookStore
from steam_client import fetch_all_live_games


def _proc_count(pattern: str) -> int:
    try:
        out = subprocess.check_output(["pgrep", "-f", pattern], text=True)
    except subprocess.CalledProcessError:
        return 0
    return len([line for line in out.splitlines() if line.strip()])


def _heartbeat_age(path: str) -> float | None:
    try:
        return time.time() - float(Path(path).read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _csv_rows(path: str) -> list[dict[str, str]]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _latest_for_match(rows: list[dict[str, str]], match_ids: set[str]) -> list[dict[str, str]]:
    if not match_ids:
        return []
    return [row for row in rows if str(row.get("match_id") or "") in match_ids]


def _paper_fill_smoke() -> dict[str, Any]:
    token = "VERIFY_PAPER_YES"
    opposing = "VERIFY_PAPER_NO"
    store = BookStore()
    store.update_direct(token, best_bid=0.49, best_ask=0.50, bid_size=100.0, ask_size=100.0)
    trader = PaperTrader()
    signal = {
        "event_type": "VERIFY_PAPER",
        "ask": 0.50,
        "fair_price": 0.70,
        "expected_move": 0.10,
        "lag": 0.10,
        "target_size_usd": min(5.0, PAPER_TRADE_SIZE_USD),
        "game_time_sec": 1200,
    }
    pos, reason = trader.enter(
        signal=signal,
        token_id=token,
        side="YES",
        book_store=store,
        match_id="VERIFY_MATCH",
        market_name="Verification Synthetic Paper Market",
        opposing_token_id=opposing,
    )
    return {
        "filled": pos is not None,
        "reason": reason,
        "entry_price": None if pos is None else pos.entry_price,
        "shares": None if pos is None else pos.shares,
        "cost_usd": None if pos is None else pos.cost_usd,
    }


async def _executor_paper_smoke() -> dict[str, Any]:
    """Exercise LiveExecutor's live-disabled paper fill branch in-process."""
    import live_executor

    saved_calls: list[tuple[Any, ...]] = []
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    original = {
        "load_live_state": live_executor.load_live_state,
        "save_live_state": live_executor.save_live_state,
        "ENABLE_REAL_LIVE_TRADING": live_executor.ENABLE_REAL_LIVE_TRADING,
        "ALLOW_EVENT_TRADES": live_executor.ALLOW_EVENT_TRADES,
        "TRADE_EVENTS": live_executor.TRADE_EVENTS,
        "MAX_OPEN_POSITIONS": live_executor.MAX_OPEN_POSITIONS,
        "MAX_TOTAL_LIVE_USD": live_executor.MAX_TOTAL_LIVE_USD,
        "MAX_TRADE_USD": live_executor.MAX_TRADE_USD,
        "MAX_OPEN_USD_PER_MATCH": live_executor.MAX_OPEN_USD_PER_MATCH,
    }
    try:
        live_executor.load_live_state = lambda: {
            "total_submitted_usd": 0.0,
            "total_filled_usd": 0.0,
            "open_positions": 0,
            "daily_realized_pnl_usd": 0.0,
            "last_reset_date": today,
            "submitted_match_sides": {},
            "submitted_match_usd": {},
        }
        live_executor.save_live_state = lambda *args, **_kwargs: saved_calls.append(args)
        live_executor.ENABLE_REAL_LIVE_TRADING = False
        live_executor.ALLOW_EVENT_TRADES = True
        live_executor.TRADE_EVENTS = {"POLL_VALUE_DISAGREEMENT"}
        live_executor.MAX_OPEN_POSITIONS = 10
        live_executor.MAX_TOTAL_LIVE_USD = 1000.0
        live_executor.MAX_TRADE_USD = 1.0
        live_executor.MAX_OPEN_USD_PER_MATCH = 100.0

        token = "VERIFY_EXECUTOR_YES"
        opposing = "VERIFY_EXECUTOR_NO"
        store = BookStore()
        store.update_direct(token, best_bid=0.48, best_ask=0.50, bid_size=100.0, ask_size=100.0)
        executor = live_executor.LiveExecutor(client=None)
        attempt = await executor.try_buy(
            signal={
                "event_type": "POLL_VALUE_DISAGREEMENT",
                "cluster_event_types": "POLL_VALUE_DISAGREEMENT",
                "event_direction": "radiant",
                "token_id": token,
                "side": "YES",
                "fair_price": 0.70,
                "ask": 0.50,
                "executable_edge": 0.20,
                "lag": 0.10,
                "spread": 0.02,
                "book_age_ms": 0,
                "steam_age_ms": 0,
                "event_schema_version": "cadence_v1",
                "source_cadence_quality": "normal",
                "event_quality": 1.0,
                "max_fill_price": 0.70,
            },
            mapping={
                "name": "Verification Team A vs Team B Game 1",
                "market_type": "MAP_WINNER",
                "yes_team": "Team A",
                "no_team": "Team B",
                "yes_token_id": token,
                "no_token_id": opposing,
                "dota_match_id": "VERIFY_EXECUTOR_MATCH",
                "confidence": 1.0,
                "tick_size": "0.01",
                "neg_risk": False,
            },
            game={
                "match_id": "VERIFY_EXECUTOR_MATCH",
                "received_at_ns": time.time_ns(),
                "game_over": False,
                "game_time_sec": 1200,
                "radiant_team": "Team A",
                "dire_team": "Team B",
                "radiant_lead": 1000,
            },
            book_store=store,
        )
        return {
            "filled": attempt.order_status == "filled",
            "order_status": attempt.order_status,
            "reason": attempt.reason_if_rejected,
            "submitted_size_usd": attempt.submitted_size_usd,
            "filled_size_usd": attempt.filled_size_usd,
            "avg_fill_price": attempt.avg_fill_price,
            "state_save_calls": len(saved_calls),
            "real_live_enabled_in_smoke": live_executor.ENABLE_REAL_LIVE_TRADING,
        }
    finally:
        for name, value in original.items():
            setattr(live_executor, name, value)


async def _live_overlap(active_mappings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    active_ids = {str(m.get("dota_match_id")) for m in active_mappings if m.get("dota_match_id")}
    async with aiohttp.ClientSession() as session:
        games = await fetch_all_live_games(session)
    live_ids = {str(g.get("match_id")) for g in games if g.get("match_id")}
    return games, sorted(active_ids & live_ids)


async def build_report() -> dict[str, Any]:
    mappings, errors = load_valid_mappings()
    active = [
        m for m in mappings
        if is_active_strategy_mapping(
            m,
            enable_match_winner_game3_proxy=True,
            enable_match_winner_research=False,
            enable_match_winner_trading=True,
        )
    ]
    games, overlap = await _live_overlap(active)
    overlap_set = set(overlap)

    signals = _csv_rows("logs/signals.csv")
    value_attempts = _csv_rows("logs/value_attempts.csv")
    paper_attempts = _csv_rows("logs/paper_attempts.csv")
    paper_trades = _csv_rows("logs/paper_trades.csv")
    overlap_signals = _latest_for_match(signals, overlap_set)
    overlap_value = _latest_for_match(value_attempts, overlap_set)

    recent_overlap_signals = overlap_signals[-10:]
    clean_overlap_signals = [
        row for row in recent_overlap_signals
        if row.get("mapping_confidence") == "1.0" and not row.get("mapping_errors")
    ]

    return {
        "mode": os.getenv("MODE", "paper"),
        "live_trading": LIVE_TRADING,
        "enable_real_live_trading": ENABLE_REAL_LIVE_TRADING,
        "processes": {
            "supervisor": _proc_count("supervisor.py"),
            "main": _proc_count("main.py"),
            "binder": _proc_count("auto_series_binder.py --loop"),
            "shadow": _proc_count("settlement_shadow.py --loop"),
            "monitor": _proc_count("monitor.py --loop"),
        },
        "heartbeat_age_sec": {
            "bot": _heartbeat_age("logs/heartbeat"),
            "binder": _heartbeat_age("logs/binder_heartbeat"),
            "shadow": _heartbeat_age("logs/shadow_heartbeat"),
            "monitor": _heartbeat_age("logs/monitor_heartbeat"),
        },
        "mappings": {
            "valid": len(mappings),
            "errors": len(errors),
            "active": len(active),
            "active_match_winner": sum(1 for m in active if m.get("market_type") == "MATCH_WINNER"),
            "active_map_winner": sum(1 for m in active if m.get("market_type") == "MAP_WINNER"),
        },
        "live": {
            "steam_games": len(games),
            "active_overlap": len(overlap),
            "overlap_match_ids": overlap[:20],
        },
        "logs": {
            "signals": len(signals),
            "value_attempts": len(value_attempts),
            "paper_attempts": len(paper_attempts),
            "paper_trades": len(paper_trades),
            "recent_overlap_signals": len(recent_overlap_signals),
            "recent_clean_overlap_signals": len(clean_overlap_signals),
            "latest_overlap_signal": recent_overlap_signals[-1] if recent_overlap_signals else None,
            "latest_overlap_value_attempt": overlap_value[-1] if overlap_value else None,
        },
        "paper_fill_smoke": _paper_fill_smoke(),
        "executor_paper_smoke": await _executor_paper_smoke(),
    }


def _ok(report: dict[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if str(report["mode"]).lower() != "paper":
        failures.append("MODE is not paper")
    if report["enable_real_live_trading"]:
        failures.append("ENABLE_REAL_LIVE_TRADING is true")
    for name, count in report["processes"].items():
        if count < 1:
            failures.append(f"{name} process not running")
    heartbeat_limits = {
        "bot": 180.0,
        "binder": 150.0,
        "shadow": 450.0,
        "monitor": float(os.getenv("MONITOR_INTERVAL_SEC", "300")) * 2 + 30,
    }
    for name, age in report["heartbeat_age_sec"].items():
        max_age = heartbeat_limits.get(name, 300.0)
        if age is None or age > max_age:
            failures.append(f"{name} heartbeat stale/missing")
    if report["mappings"]["active"] <= 0:
        failures.append("no active mappings")
    if report["live"]["active_overlap"] > 0 and report["logs"]["recent_clean_overlap_signals"] <= 0:
        failures.append("live overlap exists but no recent clean mapped signal rows")
    if not report["paper_fill_smoke"]["filled"]:
        failures.append(f"paper fill smoke failed: {report['paper_fill_smoke']['reason']}")
    if not report["executor_paper_smoke"]["filled"]:
        failures.append(f"executor paper smoke failed: {report['executor_paper_smoke']['reason']}")
    return not failures, failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Emit raw JSON only")
    args = parser.parse_args()

    report = asyncio.run(build_report())
    ok, failures = _ok(report)
    report["ok"] = ok
    report["failures"] = failures

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print(f"paper_runtime_ok={ok}")
        if failures:
            for failure in failures:
                print(f"FAIL: {failure}")
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
