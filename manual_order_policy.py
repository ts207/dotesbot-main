from __future__ import annotations

from config import (
    MAX_TRADE_USD,
    MAX_TOTAL_LIVE_USD,
    MAX_OPEN_POSITIONS,
    MAX_DAILY_DRAWDOWN_USD,
    MAX_OPEN_USD_PER_MATCH,
    MAX_SPREAD,
    MAX_BOOK_AGE_MS,
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
    token_id: str,
    mapping: dict,
    book: dict,
    ask: float,
    price_cap: float,
    operator: str,
    source: str,
    pre_trade_book: dict,
) -> PolicyResult:
    if mapping.get("mapping_state") == "quarantined":
        reason = str(mapping.get("quarantine_reason") or "unknown")
        return reject(f"mapping_quarantined:{reason}")

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

    if token_id not in (mapping.get("yes_token_id"), mapping.get("no_token_id")):
        return reject("token_not_in_mapping")
    if str(mapping.get("market_type") or "").upper() not in {"MAP_WINNER", "MATCH_WINNER"}:
        return reject("unsupported_market_type")

    import time
    book_age_ms = (time.time_ns() - book.get("received_at_ns", 0)) / 1e6
    if book_age_ms > MAX_BOOK_AGE_MS:
        return reject("book_stale")

    bid = book.get("best_bid")
    if bid is not None and ask is not None:
        spread = ask - bid
        if spread > MAX_SPREAD:
            return reject("spread_too_wide")
            
    if price_cap < ask:
        return reject("price_cap_below_ask")
    if price_cap > 0.99:
        return reject("price_cap_exceeds_max")

    return allow(reason="manual_allowed", size_usd=size_usd)
