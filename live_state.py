from __future__ import annotations

import time
from datetime import datetime, timezone
from storage_v2 import StorageV2

def load_live_state() -> dict:
    """Load persisted live risk state from SQLite via StorageV2."""
    default = {
        "total_submitted_usd": 0.0,
        "total_filled_usd": 0.0,
        "open_positions": 0,
        "daily_realized_pnl_usd": 0.0,
        "last_reset_date": "",
        "submitted_match_sides": {},
        "submitted_match_usd": {},
        "submitted_family_usd": {},
        "updated_at_ns": 0
    }
    storage = StorageV2()
    # Try to load today's state
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    loaded = storage.load_daily_budget(today_str)
    
    if loaded:
        return loaded
        
    # If today doesn't exist, we fallback to default (empty for today)
    # The caller will naturally set last_reset_date to today_str
    # Note: we might want to carry over open_positions if we were tracking them,
    # but the executor normally queries LivePositionStore for open_positions anyway.
    return default

def save_live_state(
    total_submitted_usd: float,
    total_filled_usd: float,
    open_positions: int,
    daily_realized_pnl_usd: float = 0.0,
    last_reset_date: str = "",
    submitted_match_sides: dict | None = None,
    submitted_match_usd: dict | None = None,
    submitted_family_usd: dict | None = None,
):
    """Persist live risk state to SQLite."""
    date_str = last_reset_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state = {
        "total_submitted_usd": float(total_submitted_usd),
        "total_filled_usd": float(total_filled_usd),
        "open_positions": int(open_positions),
        "daily_realized_pnl_usd": float(daily_realized_pnl_usd),
        "last_reset_date": str(last_reset_date),
        "submitted_match_sides": submitted_match_sides or {},
        "submitted_match_usd": submitted_match_usd or {},
        "submitted_family_usd": submitted_family_usd or {},
        "updated_at_ns": time.time_ns()
    }
    storage = StorageV2()
    storage.save_daily_budget(date_str, state)
