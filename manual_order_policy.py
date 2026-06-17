from __future__ import annotations

from config import (
    MAX_TRADE_USD,
    MAX_TOTAL_LIVE_USD,
    MAX_OPEN_POSITIONS,
    MAX_DAILY_DRAWDOWN_USD,
    MAX_OPEN_USD_PER_MATCH,
)
from execution_policy import PolicyResult, allow, reject

def evaluate_manual_policy(
    *,
    size_usd: float,
    match_used_usd: float,
    total_submitted_usd: float,
    open_positions: int,
    daily_realized_pnl_usd: float,
    remaining_budget_usd: float,
) -> PolicyResult:
    if size_usd <= 0:
        return reject("size <= 0")
    if size_usd > MAX_TRADE_USD:
        return reject("MAX_TRADE_USD exceeded")
    if total_submitted_usd + size_usd > MAX_TOTAL_LIVE_USD:
        return reject("MAX_TOTAL_LIVE_USD exceeded")
    if open_positions >= MAX_OPEN_POSITIONS:
        return reject("MAX_OPEN_POSITIONS exceeded")
    if daily_realized_pnl_usd < -MAX_DAILY_DRAWDOWN_USD:
        return reject("MAX_DAILY_DRAWDOWN_USD exceeded")
    if match_used_usd + size_usd > MAX_OPEN_USD_PER_MATCH:
        return reject(f"MAX_OPEN_USD_PER_MATCH exceeded: used={match_used_usd:.2f} size={size_usd:.2f} cap={MAX_OPEN_USD_PER_MATCH:.2f}")
    if remaining_budget_usd < size_usd:
        return reject(f"insufficient budget: remaining={remaining_budget_usd:.2f} size={size_usd:.2f}")

    return allow(reason="manual_allowed", size_usd=size_usd)
