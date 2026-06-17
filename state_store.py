from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Mapping


DEFAULT_STATE_DB_PATH = "logs/state.sqlite"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


class StateStore:
    """SQLite mirror for bot state.

    JSON remains source of truth for live positions during the dual-write phase.
    This store gives restart/debug tooling a queryable mirror and lets a checker
    compare JSON against SQLite before any source-of-truth migration.
    """

    def __init__(self, path: str = DEFAULT_STATE_DB_PATH):
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.init_schema()

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def init_schema(self) -> None:
        schema = SCHEMA_PATH.read_text()
        with self.connect() as conn:
            conn.executescript(schema)

    def mirror_live_positions(self, positions: Iterable[Any]) -> None:
        now_ns = time.time_ns()
        rows = []
        seen: set[str] = set()
        for pos in positions:
            data = asdict(pos) if hasattr(pos, "__dataclass_fields__") else dict(pos)
            position_id = str(data["position_id"])
            seen.add(position_id)
            rows.append(
                (
                    position_id,
                    str(data.get("state") or ""),
                    str(data.get("token_id") or ""),
                    str(data.get("match_id") or ""),
                    data.get("side"),
                    data.get("strategy_kind"),
                    data.get("strategy_family"),
                    json.dumps(data, sort_keys=True),
                    now_ns,
                )
            )
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO live_positions (
                  position_id, state, token_id, match_id, side,
                  strategy_kind, strategy_family, raw_json, updated_at_ns
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(position_id) DO UPDATE SET
                  state=excluded.state,
                  token_id=excluded.token_id,
                  match_id=excluded.match_id,
                  side=excluded.side,
                  strategy_kind=excluded.strategy_kind,
                  strategy_family=excluded.strategy_family,
                  raw_json=excluded.raw_json,
                  updated_at_ns=excluded.updated_at_ns
                """,
                rows,
            )
            if seen:
                placeholders = ",".join("?" for _ in seen)
                conn.execute(
                    f"DELETE FROM live_positions WHERE position_id NOT IN ({placeholders})",
                    tuple(seen),
                )
            else:
                conn.execute("DELETE FROM live_positions")

    def live_positions(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT raw_json FROM live_positions"
        params: tuple[Any, ...] = ()
        if active_only:
            sql += " WHERE state IN (?, ?, ?, ?, ?)"
            params = ("OPEN", "PARTIALLY_EXITED", "PENDING_ENTRY", "PENDING_EXIT_GTC", "EXITING")
        with self.connect() as conn:
            return [json.loads(row[0]) for row in conn.execute(sql, params)]

    def compare_live_positions_json(self, json_positions: Iterable[Any]) -> dict[str, Any]:
        json_ids = {
            str((asdict(p) if hasattr(p, "__dataclass_fields__") else dict(p)).get("position_id"))
            for p in json_positions
        }
        sqlite_ids = {str(row["position_id"]) for row in self.live_positions(active_only=False)}
        return {
            "json_count": len(json_ids),
            "sqlite_count": len(sqlite_ids),
            "missing_in_sqlite": sorted(json_ids - sqlite_ids),
            "extra_in_sqlite": sorted(sqlite_ids - json_ids),
        }

    def record_live_attempt(self, row: Mapping[str, Any]) -> None:
        now_ns = time.time_ns()
        raw = json.dumps(dict(row), sort_keys=True, default=str)
        token_id = str(row.get("token_id") or "")
        match_id = str(row.get("match_id") or "")
        order_id = str(row.get("order_id") or row.get("raw_response_order_id") or "")
        if not order_id:
            order_id = f"{match_id}|{token_id}|{row.get('timestamp_utc') or now_ns}|{row.get('phase') or 'attempt'}"
        policy_id = f"{order_id}|policy"
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO live_orders (
                  order_id, position_id, token_id, match_id, state, raw_json, updated_at_ns
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                  token_id=excluded.token_id,
                  match_id=excluded.match_id,
                  state=excluded.state,
                  raw_json=excluded.raw_json,
                  updated_at_ns=excluded.updated_at_ns
                """,
                (
                    order_id,
                    row.get("position_id"),
                    token_id,
                    match_id,
                    row.get("order_status") or row.get("phase") or "",
                    raw,
                    now_ns,
                ),
            )
            conn.execute(
                """
                INSERT INTO policy_decisions (
                  policy_id, match_id, token_id, allowed, reason,
                  policy_version, raw_json, created_at_ns
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(policy_id) DO UPDATE SET
                  allowed=excluded.allowed,
                  reason=excluded.reason,
                  policy_version=excluded.policy_version,
                  raw_json=excluded.raw_json
                """,
                (
                    policy_id,
                    match_id,
                    token_id,
                    _bool_int(row.get("policy_allowed")),
                    row.get("policy_reason") or row.get("reason_if_rejected") or "",
                    row.get("policy_version") or "",
                    raw,
                    now_ns,
                ),
            )

    def record_strategy_signal(self, row: Mapping[str, Any]) -> None:
        now_ns = time.time_ns()
        signal_id = str(row.get("signal_id") or "")
        if not signal_id:
            signal_id = f"{row.get('strategy') or row.get('strategy_kind') or 'strategy'}|{row.get('match_id') or ''}|{row.get('token_id') or ''}|{row.get('timestamp_utc') or now_ns}"
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO strategy_signals (
                  signal_id, match_id, strategy_kind, token_id, raw_json, created_at_ns
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(signal_id) DO UPDATE SET
                  match_id=excluded.match_id,
                  strategy_kind=excluded.strategy_kind,
                  token_id=excluded.token_id,
                  raw_json=excluded.raw_json
                """,
                (
                    signal_id,
                    row.get("match_id") or "",
                    row.get("strategy") or row.get("strategy_kind") or "",
                    row.get("token_id") or "",
                    json.dumps(dict(row), sort_keys=True, default=str),
                    now_ns,
                ),
            )

    def record_allocation_decision(self, row: Mapping[str, Any]) -> None:
        now_ns = time.time_ns()
        decision_id = f"{row.get('match_id') or ''}|{row.get('token_id') or ''}|{row.get('timestamp_utc') or now_ns}"
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO allocation_decisions (
                  decision_id, match_id, strategy_kind, raw_json, created_at_ns
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(decision_id) DO UPDATE SET
                  match_id=excluded.match_id,
                  strategy_kind=excluded.strategy_kind,
                  raw_json=excluded.raw_json
                """,
                (
                    decision_id,
                    row.get("match_id") or "",
                    row.get("winner_strategy") or "",
                    json.dumps(dict(row), sort_keys=True, default=str),
                    now_ns,
                ),
            )

    def record_mapping_snapshots(self, mappings: Iterable[Mapping[str, Any]]) -> None:
        rows = []
        for idx, mapping in enumerate(mappings):
            now_ns = time.time_ns()
            market_id = str(mapping.get("market_id") or "")
            condition_id = str(mapping.get("condition_id") or "")
            dota_match_id = str(mapping.get("dota_match_id") or "")
            snapshot_id = f"{market_id or condition_id or dota_match_id}|{now_ns}|{idx}"
            rows.append(
                (
                    snapshot_id,
                    market_id,
                    condition_id,
                    dota_match_id,
                    str(mapping.get("mapping_state") or ""),
                    json.dumps(dict(mapping), sort_keys=True, default=str),
                    now_ns,
                )
            )
        if not rows:
            return
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO mapping_snapshots (
                  snapshot_id, market_id, condition_id, dota_match_id,
                  mapping_state, raw_json, created_at_ns
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )


def _bool_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return int(value)
    return int(str(value).strip().lower() in {"1", "true", "yes", "on"})
