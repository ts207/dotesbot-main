"""SQLite state-table access for data_v2/operational.db.

Public API:
    init_db()        — create or upgrade the schema (idempotent)
    connect()        — return a sqlite3.Connection with the right pragmas
    upsert_position(...)
    update_budget(...)
    upsert_market_mapping(...)
    upsert_league(...)

Convention: every public function opens its own short-lived connection
unless an explicit `conn` is passed. This keeps the call sites simple at
the cost of a per-call open; for hot loops the caller passes `conn`.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from .paths import SQLITE_PATH

_SCHEMA_SQL_PATH = Path(__file__).parent / "schema.sql"


def init_db(path: Path | str = SQLITE_PATH) -> None:
    """Create the database file and all tables. Idempotent — safe to
    call on every bot start."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sql = _SCHEMA_SQL_PATH.read_text()
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()


def connect(path: Path | str = SQLITE_PATH) -> sqlite3.Connection:
    """Return a connection with row_factory set so callers can use
    `row["column_name"]`."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
