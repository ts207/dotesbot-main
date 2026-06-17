import sqlite3
import os
import json
import time
import logging
from typing import Any, List, Dict, Iterable

DEFAULT_DB_PATH = "logs/state_v2.sqlite"
logger = logging.getLogger(__name__)

ACTIVE_POSITION_STATES = frozenset({
    "OPEN",
    "PARTIALLY_EXITED",
    "PENDING_ENTRY",
    "PENDING_EXIT_GTC",
    "EXITING",
})

class StorageV2:
    """SQLite backend for bot state, replacing live_positions.json and paper_trades.csv."""
    def __init__(self, path: str = None):
        self.path = path or DEFAULT_DB_PATH
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        schema = """
        CREATE TABLE IF NOT EXISTS matches (
            id TEXT PRIMARY KEY,
            start_time_ns INTEGER,
            match_winner TEXT
        );

        CREATE TABLE IF NOT EXISTS positions (
            position_id TEXT PRIMARY KEY,
            token_id TEXT,
            match_id TEXT,
            entry_px REAL,
            shares REAL,
            size_usd REAL,
            side TEXT,
            mode TEXT,
            state TEXT,
            raw_json TEXT,
            updated_at_ns INTEGER
        );

        CREATE TABLE IF NOT EXISTS closed_positions (
            position_id TEXT PRIMARY KEY,
            token_id TEXT,
            match_id TEXT,
            entry_px REAL,
            exit_px REAL,
            shares REAL,
            size_usd REAL,
            pnl_usd REAL,
            side TEXT,
            mode TEXT,
            exit_reason TEXT,
            exit_time_ns INTEGER,
            raw_json TEXT
        );

        CREATE TABLE IF NOT EXISTS daily_budgets (
            date_str TEXT NOT NULL,
            mode TEXT NOT NULL,
            total_submitted_usd REAL,
            total_filled_usd REAL,
            open_positions INTEGER,
            daily_realized_pnl_usd REAL,
            submitted_match_sides TEXT,
            submitted_match_usd TEXT,
            submitted_family_usd TEXT,
            updated_at_ns INTEGER,
            PRIMARY KEY (date_str, mode)
        );
        """
        with self.connect() as conn:
            # Migration check for daily_budgets
            cursor = conn.execute("PRAGMA table_info(daily_budgets)")
            columns = [row["name"] for row in cursor.fetchall()]
            
            # If the table exists but doesn't have a mode column, we need to migrate it
            if columns and "mode" not in columns:
                conn.execute("ALTER TABLE daily_budgets RENAME TO daily_budgets_legacy_date_only")
                conn.executescript(schema)
                
                # Copy old rows to the new table under the 'legacy' mode
                conn.execute("""
                    INSERT INTO daily_budgets (
                        date_str, mode, total_submitted_usd, total_filled_usd,
                        open_positions, daily_realized_pnl_usd, submitted_match_sides,
                        submitted_match_usd, submitted_family_usd, updated_at_ns
                    )
                    SELECT
                        date_str, 'legacy', total_submitted_usd, total_filled_usd,
                        open_positions, daily_realized_pnl_usd, submitted_match_sides,
                        submitted_match_usd, submitted_family_usd, updated_at_ns
                    FROM daily_budgets_legacy_date_only
                """)
            else:
                conn.executescript(schema)

    # --- Positions (Live & Paper) ---
    def save_position(self, pos_dict: dict, mode: str):
        """Upsert a position into the positions table."""
        now_ns = time.time_ns()
        position_id = str(pos_dict.get("position_id") or pos_dict.get("token_id"))
        
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO positions (
                    position_id, token_id, match_id, entry_px, shares, size_usd,
                    side, mode, state, raw_json, updated_at_ns
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(position_id) DO UPDATE SET
                    token_id=excluded.token_id,
                    match_id=excluded.match_id,
                    entry_px=excluded.entry_px,
                    shares=excluded.shares,
                    size_usd=excluded.size_usd,
                    side=excluded.side,
                    mode=excluded.mode,
                    state=excluded.state,
                    raw_json=excluded.raw_json,
                    updated_at_ns=excluded.updated_at_ns
                """,
                (
                    position_id,
                    str(pos_dict.get("token_id", "")),
                    str(pos_dict.get("match_id", "")),
                    float(pos_dict.get("entry_price", 0.0)),
                    float(pos_dict.get("shares", 0.0)),
                    float(pos_dict.get("cost_usd", 0.0)),
                    str(pos_dict.get("side", "")),
                    mode,
                    str(pos_dict.get("state", "OPEN")),
                    json.dumps(pos_dict, sort_keys=True, default=str),
                    now_ns
                )
            )

    def load_positions(self, mode: str, *, active_only: bool = False) -> List[dict]:
        """Load all open positions for a given mode."""
        with self.connect() as conn:
            if active_only:
                placeholders = ",".join("?" * len(ACTIVE_POSITION_STATES))
                rows = conn.execute(
                    f"SELECT raw_json FROM positions WHERE mode = ? AND state IN ({placeholders})",
                    (mode, *sorted(ACTIVE_POSITION_STATES)),
                ).fetchall()
            else:
                rows = conn.execute("SELECT raw_json FROM positions WHERE mode = ?", (mode,)).fetchall()
            return [json.loads(row["raw_json"]) for row in rows]

    def remove_position(self, position_id: str):
        with self.connect() as conn:
            conn.execute("DELETE FROM positions WHERE position_id = ?", (position_id,))

    def save_closed_position(self, pos_dict: dict, mode: str):
        now_ns = time.time_ns()
        position_id = str(pos_dict.get("position_id") or pos_dict.get("token_id"))
        
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO closed_positions (
                    position_id, token_id, match_id, entry_px, exit_px, shares,
                    size_usd, pnl_usd, side, mode, exit_reason, exit_time_ns, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(position_id) DO UPDATE SET
                    exit_px=excluded.exit_px,
                    pnl_usd=excluded.pnl_usd,
                    exit_reason=excluded.exit_reason,
                    exit_time_ns=excluded.exit_time_ns,
                    raw_json=excluded.raw_json
                """,
                (
                    position_id,
                    str(pos_dict.get("token_id", "")),
                    str(pos_dict.get("match_id", "")),
                    float(pos_dict.get("entry_price", 0.0)),
                    float(pos_dict.get("exit_price", 0.0)),
                    float(pos_dict.get("shares", 0.0)),
                    float(pos_dict.get("cost_usd", 0.0)),
                    float(pos_dict.get("pnl_usd", 0.0)),
                    str(pos_dict.get("side", "")),
                    mode,
                    str(pos_dict.get("exit_reason", "")),
                    int(pos_dict.get("exit_time_ns") or now_ns),
                    json.dumps(pos_dict, sort_keys=True, default=str)
                )
            )

    def load_closed_positions(self, mode: str) -> List[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT raw_json FROM closed_positions WHERE mode = ?", (mode,)).fetchall()
            return [json.loads(row["raw_json"]) for row in rows]

    # --- Live State (Budgets) ---
    _ALLOWED_BUDGET_MODES = {"dry_live", "real_live", "legacy"}

    def _normalize_budget_mode(self, mode: str) -> str:
        if mode not in self._ALLOWED_BUDGET_MODES:
            raise ValueError(f"Invalid budget mode: {mode}")
        return mode

    def save_daily_budget(self, date_str: str, data: dict, mode: str):
        now_ns = time.time_ns()
        mode = self._normalize_budget_mode(mode)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_budgets (
                    date_str, mode, total_submitted_usd, total_filled_usd, open_positions,
                    daily_realized_pnl_usd, submitted_match_sides, submitted_match_usd,
                    submitted_family_usd, updated_at_ns
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date_str, mode) DO UPDATE SET
                    total_submitted_usd=excluded.total_submitted_usd,
                    total_filled_usd=excluded.total_filled_usd,
                    open_positions=excluded.open_positions,
                    daily_realized_pnl_usd=excluded.daily_realized_pnl_usd,
                    submitted_match_sides=excluded.submitted_match_sides,
                    submitted_match_usd=excluded.submitted_match_usd,
                    submitted_family_usd=excluded.submitted_family_usd,
                    updated_at_ns=excluded.updated_at_ns
                """,
                (
                    date_str,
                    mode,
                    float(data.get("total_submitted_usd", 0.0)),
                    float(data.get("total_filled_usd", 0.0)),
                    int(data.get("open_positions", 0)),
                    float(data.get("daily_realized_pnl_usd", 0.0)),
                    json.dumps(data.get("submitted_match_sides", {})),
                    json.dumps(data.get("submitted_match_usd", {})),
                    json.dumps(data.get("submitted_family_usd", {})),
                    now_ns
                )
            )

    def load_daily_budget(self, date_str: str, mode: str) -> dict | None:
        mode = self._normalize_budget_mode(mode)
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM daily_budgets WHERE date_str = ? AND mode = ?", (date_str, mode)).fetchone()
            if not row:
                return None
            return {
                "last_reset_date": row["date_str"],
                "total_submitted_usd": row["total_submitted_usd"],
                "total_filled_usd": row["total_filled_usd"],
                "open_positions": row["open_positions"],
                "daily_realized_pnl_usd": row["daily_realized_pnl_usd"],
                "submitted_match_sides": json.loads(row["submitted_match_sides"] or "{}"),
                "submitted_match_usd": json.loads(row["submitted_match_usd"] or "{}"),
                "submitted_family_usd": json.loads(row["submitted_family_usd"] or "{}"),
            }
