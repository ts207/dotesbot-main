from __future__ import annotations

import time
import csv
from datetime import datetime, timezone
from typing import Any

class DictObj:
    def __init__(self, d):
        for k, v in d.items():
            setattr(self, k, v)

def position_obj_from_dict(d: dict) -> Any:
    return DictObj(d)

def build_exit_observation_row(
    *,
    position: dict,
    book: dict | None,
    game: dict | None = None,
    game_over_match_ids: set[str] = None,
    actual_exit_reason: str | None = None,
    actual_exit_price: float | None = None,
    settlement_price: float | None = None,
    now_ns: int | None = None,
) -> dict:
    now_ns = now_ns or time.time_ns()
    game_over_match_ids = game_over_match_ids or set()
    
    # Metadata
    row = {
        "timestamp_utc": datetime.fromtimestamp(now_ns / 1e9, tz=timezone.utc).isoformat(),
        "position_id": position.get("position_id") or f"{position.get('match_id')}:{position.get('token_id')}:{position.get('entry_time_ns')}",
        "mode": str(position.get("paper_mode") or "paper").lower(),
        "match_id": position.get("match_id"),
        "token_id": position.get("token_id"),
        "side": position.get("side"),
        "strategy_family": position.get("strategy_family"),
        "strategy_kind": position.get("strategy_kind"),
        "hold_policy": position.get("hold_policy"),
    }
    
    # Price/State
    entry_price = float(position.get("entry_price") or 0.0)
    bid = float(book.get("best_bid")) if book and book.get("best_bid") is not None else None
    ask = float(book.get("best_ask")) if book and book.get("best_ask") is not None else None
    shares = float(position.get("shares") or 0.0)
    cost_usd = float(position.get("cost_usd") or 0.0)
    entry_time_ns = int(position.get("entry_time_ns") or 0)
    
    row.update({
        "entry_price": entry_price,
        "current_bid": bid,
        "current_ask": ask,
        "shares": shares,
        "cost_usd": cost_usd,
        "entry_time_ns": entry_time_ns,
        "age_sec": (now_ns - entry_time_ns) / 1e9 if entry_time_ns else 0.0,
    })
    
    # Actual Outcome
    actual_pnl = None
    if actual_exit_price is not None and shares > 0:
        actual_pnl = (actual_exit_price * shares) - cost_usd
        
    row.update({
        "actual_exit_reason": actual_exit_reason,
        "actual_exit_price": actual_exit_price,
        "actual_pnl_usd": actual_pnl,
    })
    
    # Triggers (Counterfactuals)
    import config
    from exit_policy import _radiant_lead, _fair_invalidates
    
    catastrophe_triggered = False
    if bid is not None and 0.0 < config.CATASTROPHE_FLOOR and bid < config.CATASTROPHE_FLOOR:
        backed_direction = position.get("backed_direction") or position.get("entry_backed_side")
        radiant_lead = _radiant_lead(game)
        if backed_direction in {"radiant", "dire"} and radiant_lead is not None:
            backed_lead = radiant_lead if backed_direction == "radiant" else -radiant_lead
            if backed_lead < -config.CATASTROPHE_NW_CONFIRM:
                catastrophe_triggered = True
        elif game is None:
            catastrophe_triggered = True

    current_fair = position.get("fair_price")
    if current_fair is not None and current_fair <= 0:
        current_fair = None
    
    fair_invalidated = False
    if bid is not None and _fair_invalidates(position_obj_from_dict(position), bid, current_fair):
        fair_invalidated = True

    game_over = position.get("match_id") in game_over_match_ids
    
    max_hold_sec = config.MAX_HOLD_HOURS * 3600
    age_sec = row["age_sec"]
    max_hold_triggered = age_sec >= max_hold_sec

    row.update({
        "catastrophe_salvage_triggered": catastrophe_triggered,
        "fair_invalidation_triggered": fair_invalidated,
        "map_end_convergence_triggered": game_over, # DSWING/convergence trigger
        "game_over_triggered": game_over,
        "max_hold_triggered": max_hold_triggered,
    })
    
    # Settlement (Placeholders for Task 2/Post-processing)
    row.update({
        "settlement_price": settlement_price,
        "settlement_pnl_usd": None,
        "active_exit_delta_usd": None,
        "exit_helped": None,
    })
    
    return row

import os

# Define headers for deterministic order
OBSERVATION_HEADERS = [
    "timestamp_utc",
    "position_id",
    "mode",
    "match_id",
    "token_id",
    "side",
    "strategy_family",
    "strategy_kind",
    "hold_policy",
    "entry_price",
    "current_bid",
    "current_ask",
    "shares",
    "cost_usd",
    "entry_time_ns",
    "age_sec",
    "actual_exit_reason",
    "actual_exit_price",
    "actual_pnl_usd",
    "catastrophe_salvage_triggered",
    "fair_invalidation_triggered",
    "map_end_convergence_triggered",
    "game_over_triggered",
    "max_hold_triggered",
    "settlement_price",
    "settlement_pnl_usd",
    "active_exit_delta_usd",
    "exit_helped",
]

def write_exit_observation(row: dict, path: str = "logs/exit_policy_observations.csv") -> None:
    file_exists = os.path.exists(path)
    parent = os.path.dirname(path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)
        
    # Use headers if they are known, otherwise use sorted keys from row
    headers = OBSERVATION_HEADERS
    if not all(k in headers for k in row.keys()):
        # If row has extra keys, we might want to include them or just use row keys
        headers = sorted(row.keys())

    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)
