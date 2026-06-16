from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any

LIVE_POSITIONS_PATH = "logs/live_positions.json"


@dataclass
class LivePosition:
    position_id: str
    state: str
    token_id: str
    opposing_token_id: str
    match_id: str
    market_name: str | None
    side: str
    entry_price: float
    shares: float
    cost_usd: float
    entry_time_ns: int
    entry_game_time_sec: int | None
    event_type: str
    expected_move: float
    fair_price: float
    exit_attempts: int = 0
    last_exit_attempt_ns: int | None = None
    exit_reason: str | None = None
    pending_exit_order_id: str | None = None
    exit_order_price: float | None = None
    pending_entry_order_id: str | None = None
    is_underdog_reversal: bool = False
    peak_bid: float = 0.0
    # Strategy identity and exit horizon for active value-family positions.
    trader_kind: str = "event"
    exit_horizon_sec: int | None = None
    signal_id: str | None = None
    # which Dota side we backed ("radiant"/"dire") — lets the exit logic confirm a
    # catastrophe cut against the LIVE net-worth state, not just the token price.
    backed_direction: str | None = None
    # 2026-06-16 — PnL separation tags
    strategy_kind: str | None = None
    strategy_family: str | None = None
    strategy_subtype: str | None = None
    entry_is_reversal: bool | None = None
    entry_is_continuation: bool | None = None
    entry_engine: str | None = None
    exit_engine: str | None = None
    hold_policy: str | None = None
    entry_fair: float | None = None
    entry_edge: float | None = None
    entry_backed_side: str | None = None
    entry_radiant_lead: int | None = None
    entry_actual_event_type: str | None = None
    entry_derived_state_flags: list[str] = field(default_factory=list)


class LivePositionStore:
    def __init__(self, path: str = LIVE_POSITIONS_PATH):
        self.path = path
        self.positions: dict[str, LivePosition] = {}
        self.load()

    def load(self) -> None:
        if not os.path.exists(self.path):
            self.positions = {}
            return

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.positions = {
                row["position_id"]: LivePosition(**row)
                for row in data.get("positions", [])
            }
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            import logging
            import shutil
            logging.getLogger(__name__).critical(f"FATAL: {self.path} is corrupt ({e}). Backing up and resetting positions to empty!")
            try:
                shutil.copy2(self.path, f"{self.path}.corrupt.{int(time.time())}")
            except OSError:
                pass
            self.positions = {}

    def save(self) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        data = {
            "updated_at_ns": time.time_ns(),
            "positions": [asdict(p) for p in self.positions.values()],
        }
        from atomic_writes import atomic_json_write
        atomic_json_write(self.path, data, indent=2)

    def add(self, pos: LivePosition) -> None:
        self.positions[pos.position_id] = pos
        self.save()

    # State sets used by reconciliation: anything in these holds capital
    # or has on-chain side effects and counts toward "open positions".
    ACTIVE_STATES = frozenset({
        "OPEN", "PARTIALLY_EXITED",
        "PENDING_ENTRY", "PENDING_EXIT_GTC", "EXITING",
    })

    def open_positions(self) -> list[LivePosition]:
        return [
            p for p in self.positions.values()
            if p.state in {"OPEN", "PARTIALLY_EXITED"}
        ]

    def summarize(self) -> dict[str, int]:
        """Return {state: count} across all known positions."""
        out: dict[str, int] = {}
        for p in self.positions.values():
            out[p.state] = out.get(p.state, 0) + 1
        return out

    def active_count(self) -> int:
        """Count of positions holding capital or pending exchange side-effects."""
        return sum(1 for p in self.positions.values() if p.state in self.ACTIVE_STATES)

    def pending_gtc_positions(self) -> list[LivePosition]:
        return [
            p for p in self.positions.values()
            if p.state == "PENDING_EXIT_GTC" and p.pending_exit_order_id
        ]

    def pending_entry_positions(self) -> list[LivePosition]:
        return [
            p for p in self.positions.values()
            if p.state == "PENDING_ENTRY" and p.pending_entry_order_id
        ]

    def mark_exiting(self, position_id: str, reason: str) -> None:
        if position_id not in self.positions:
            return
        p = self.positions[position_id]
        p.state = "EXITING"
        p.exit_reason = reason
        p.exit_attempts += 1
        p.last_exit_attempt_ns = time.time_ns()
        self.save()

    def mark_closed(self, position_id: str) -> None:
        if position_id not in self.positions:
            return
        p = self.positions[position_id]
        p.state = "CLOSED"
        self.save()

    def mark_open_again(self, position_id: str) -> None:
        if position_id not in self.positions:
            return
        p = self.positions[position_id]
        p.state = "OPEN"
        self.save()

    def mark_pending_exit_gtc(self, position_id: str, order_id: str, price: float) -> None:
        if position_id not in self.positions:
            return
        p = self.positions[position_id]
        p.state = "PENDING_EXIT_GTC"
        p.pending_exit_order_id = order_id
        p.exit_order_price = price
        self.save()
