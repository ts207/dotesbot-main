#!/usr/bin/env python3
"""One-shot backfill of JSON state files into data_v2/operational.db.

Mirrors all current state files into the SQLite tables so analytics, dashboards,
and ad-hoc queries can read state through a single API. JSON files remain the
source-of-truth for the running bot — this script is a read-only mirror.

Sources:
  logs/live_state.json + logs/paper_state.json    → budget (one row, merged)
  logs/live_positions.json + logs/paper_positions_v2.json → positions
  logs/team_id_cache.json                         → cache_team_id
  logs/usdc_balance.json                          → cache_balance
  markets.yaml                                    → market_mappings

Idempotent: existing rows in each table are replaced.

Usage:
    python3 scripts/backfill_state_to_sqlite.py [--table NAME]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DB_PATH = REPO_ROOT / "data_v2" / "operational.db"
LOGS = REPO_ROOT / "logs"
MARKETS_YAML = REPO_ROOT / "markets.yaml"

from team_utils import norm_team  # noqa: E402


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  warning: could not parse {path}: {exc}")
        return None


def _date_to_ns(date_str: str | None) -> int | None:
    """Convert 'YYYY-MM-DD' (last_reset_date) to a midnight-UTC ns timestamp."""
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1_000_000_000)
    except (TypeError, ValueError):
        return None


# ------------- budget -------------
def backfill_budget(conn: sqlite3.Connection) -> int:
    """Merge live_state.json + paper_state.json into a single budget row.
    We prefer paper if live is zero (typical when bot is in paper mode)."""
    live = _load_json(LOGS / "live_state.json") or {}
    paper = _load_json(LOGS / "paper_state.json") or {}
    if not live and not paper:
        return 0
    # Pick the source with more recent updated_at_ns.
    src = paper if paper.get("updated_at_ns", 0) >= live.get("updated_at_ns", 0) else live
    conn.execute("DELETE FROM budget")
    conn.execute(
        """INSERT INTO budget (
            id, total_submitted_usd, total_filled_usd, open_positions_count,
            daily_realized_pnl_usd, daily_drawdown_usd,
            last_reset_at_ns, updated_at_ns
        ) VALUES (1, ?, ?, ?, ?, 0, ?, ?)""",
        (
            float(src.get("total_submitted_usd") or 0),
            float(src.get("total_filled_usd") or 0),
            int(src.get("open_positions") or 0),
            float(src.get("daily_realized_pnl_usd") or 0),
            _date_to_ns(src.get("last_reset_date")),
            int(src.get("updated_at_ns") or time.time_ns()),
        ),
    )
    return 1


# ------------- positions -------------
def backfill_positions(conn: sqlite3.Connection) -> int:
    """Merge live_positions.json + paper_positions_v2.json into the positions
    table. Trader_kind is inferred from the source file or position record."""
    n = 0
    conn.execute("DELETE FROM positions")
    for path, default_kind in [
        (LOGS / "live_positions.json", "live"),
        (LOGS / "paper_positions_v2.json", "paper"),
    ]:
        data = _load_json(path)
        if not data:
            continue
        for pos in data.get("positions", []):
            position_id = pos.get("position_id")
            if not position_id:
                continue
            entry_time_ns = int(pos.get("entry_time_ns") or 0)
            shares = float(pos.get("shares") or 0)
            entry_price = float(pos.get("entry_price") or 0)
            notional = float(pos.get("cost_usd") or (shares * entry_price))
            state = (pos.get("state") or "OPEN").lower()
            # Normalize state values to schema vocab
            status = "open" if state in ("open", "active") else \
                     ("closed" if state in ("closed", "exited") else "partial")
            conn.execute(
                """INSERT OR REPLACE INTO positions (
                    position_id, attempt_id, signal_id, match_id, token_id,
                    side, market_name, event_type,
                    opened_at_ns, closed_at_ns,
                    entry_price, shares, notional_usd,
                    exit_price, exit_reason, realized_pnl_usd, status
                ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, NULL, ?)""",
                (
                    position_id,
                    pos.get("signal_id"),
                    str(pos.get("match_id") or ""),
                    str(pos.get("token_id") or ""),
                    pos.get("side") or "",
                    pos.get("market_name"),
                    pos.get("event_type"),
                    entry_time_ns,
                    entry_price,
                    shares,
                    notional,
                    pos.get("exit_order_price"),
                    pos.get("exit_reason"),
                    status,
                ),
            )
            n += 1
    return n


# ------------- cache_team_id -------------
def backfill_team_cache(conn: sqlite3.Connection) -> int:
    """team_id_cache.json maps {team_id → team_name}. SQLite cache_team_id is
    inverse-indexed by normalized team name. We populate both directions
    by inserting one row per (team_id, name) pair."""
    data = _load_json(LOGS / "team_id_cache.json")
    if not data:
        return 0
    conn.execute("DELETE FROM cache_team_id")
    now_ns = time.time_ns()
    n = 0
    for team_id, name in data.items():
        if not name:
            continue
        norm = norm_team(name)
        if not norm:
            continue
        try:
            conn.execute(
                """INSERT OR REPLACE INTO cache_team_id (
                    team_name_norm, team_id, canonical_name, seen_at_ns
                ) VALUES (?, ?, ?, ?)""",
                (norm, str(team_id), name, now_ns),
            )
            n += 1
        except sqlite3.IntegrityError:
            continue
    return n


# ------------- cache_balance -------------
def backfill_balance(conn: sqlite3.Connection) -> int:
    data = _load_json(LOGS / "usdc_balance.json")
    if not data:
        return 0
    conn.execute("DELETE FROM cache_balance")
    conn.execute(
        """INSERT INTO cache_balance (
            id, usdc_balance, fetched_at_ns, ttl_sec, source
        ) VALUES (1, ?, ?, 60, 'poly_clob_api')""",
        (
            float(data.get("usdc_balance") or 0),
            int(data.get("checked_at_ns") or time.time_ns()),
        ),
    )
    return 1


# ------------- market_mappings -------------
def backfill_market_mappings(conn: sqlite3.Connection) -> int:
    if not MARKETS_YAML.exists():
        return 0
    yam = yaml.safe_load(MARKETS_YAML.read_text())
    markets = yam.get("markets", [])
    conn.execute("DELETE FROM market_mappings")
    now_ns = time.time_ns()
    n = 0
    for m in markets:
        market_id = str(m.get("market_id") or "")
        if not market_id:
            continue
        try:
            conn.execute(
                """INSERT OR REPLACE INTO market_mappings (
                    market_id, condition_id, yes_token_id, no_token_id,
                    market_type, market_name, yes_team, no_team,
                    dota_match_id, steam_radiant_team, steam_dire_team,
                    steam_side_mapping, confidence,
                    effective_from_ns, effective_to_ns
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
                (
                    market_id,
                    m.get("condition_id"),
                    str(m.get("yes_token_id") or ""),
                    str(m.get("no_token_id") or ""),
                    m.get("market_type") or "MAP_WINNER",
                    m.get("name"),
                    m.get("yes_team"),
                    m.get("no_team"),
                    str(m.get("dota_match_id") or "") or None,
                    m.get("steam_radiant_team"),
                    m.get("steam_dire_team"),
                    m.get("steam_side_mapping"),
                    float(m.get("confidence") or 0),
                    now_ns,
                ),
            )
            n += 1
        except sqlite3.IntegrityError as exc:
            print(f"  skipping market {market_id}: {exc}")
            continue
    return n


def main():
    parser = argparse.ArgumentParser(description="Backfill state JSON → SQLite")
    parser.add_argument("--table", choices=["budget", "positions", "team_cache",
                                             "balance", "mappings", "all"],
                        default="all")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"FATAL: {DB_PATH} does not exist", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("BEGIN")
    try:
        results = {}
        if args.table in ("budget", "all"):
            results["budget"] = backfill_budget(conn)
        if args.table in ("positions", "all"):
            results["positions"] = backfill_positions(conn)
        if args.table in ("team_cache", "all"):
            results["cache_team_id"] = backfill_team_cache(conn)
        if args.table in ("balance", "all"):
            results["cache_balance"] = backfill_balance(conn)
        if args.table in ("mappings", "all"):
            results["market_mappings"] = backfill_market_mappings(conn)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    print("=== Backfill results ===")
    for table, n in results.items():
        print(f"  {table:<18}  {n:>5} rows written")


if __name__ == "__main__":
    main()
