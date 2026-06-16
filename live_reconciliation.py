from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from live_position_store import LivePosition, LivePositionStore

logger = logging.getLogger(__name__)

ACTIVE_STATES = {"OPEN", "PARTIALLY_EXITED", "PENDING_ENTRY", "PENDING_EXIT_GTC", "EXITING"}
DUST_SHARES = 0.01


@dataclass
class LiveReconciliationResult:
    checked_tokens: int = 0
    balance_errors: int = 0
    closed_stale: int = 0
    reopened_missing: int = 0
    adjusted_existing: int = 0
    active_after: int = 0


def _mapping_token_index(mappings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for mapping in mappings:
        yes = mapping.get("yes_token_id")
        no = mapping.get("no_token_id")
        if yes:
            index[str(yes)] = {
                "side": "YES",
                "opposing_token_id": str(no or ""),
                "market_name": mapping.get("name"),
                "match_id": str(mapping.get("dota_match_id") or mapping.get("match_id") or "unknown"),
            }
        if no:
            index[str(no)] = {
                "side": "NO",
                "opposing_token_id": str(yes or ""),
                "market_name": mapping.get("name"),
                "match_id": str(mapping.get("dota_match_id") or mapping.get("match_id") or "unknown"),
            }
    return index


async def reconcile_live_positions(
    *,
    client: Any,
    store: LivePositionStore,
    mappings: list[dict[str, Any]],
    live_executor: Any | None = None,
    live_exit_logger: Any | None = None,
    book_store: Any | None = None,
) -> LiveReconciliationResult:
    """Sync local live_positions.json with CLOB conditional-token balances.

    Startup already cancels resting orders. This pass then treats token balances
    as source of truth for whether a local position is still active.
    """
    result = LiveReconciliationResult()
    token_index = _mapping_token_index(mappings)
    active_by_token = {
        str(pos.token_id): pos
        for pos in store.positions.values()
        if pos.state in ACTIVE_STATES
    }
    # Only reconcile tokens we could actually hold: active-position tokens plus any
    # token that appears anywhere in the position store. We only ever acquire a
    # token via our own trades (which are recorded), so scanning ALL ~800 mapping
    # tokens was wasted work — and it serializes on the client lock inside
    # get_conditional_balance (so it can't be parallelized away), which is what
    # made boot take ~250s and delayed the bot reaching the trading loop.
    store_tokens = {str(p.token_id) for p in store.positions.values() if p.token_id}
    tokens = sorted(set(active_by_token) | store_tokens)
    found_balances: dict[str, float] = {}

    for token_id in tokens:
        result.checked_tokens += 1
        try:
            shares = await client.get_conditional_balance(token_id)
        except Exception as exc:
            result.balance_errors += 1
            logger.warning("[startup_reconcile] balance fetch failed token=%s: %s", token_id, exc)
            continue
        if shares is not None and shares >= DUST_SHARES:
            found_balances[token_id] = shares

    now_ns = time.time_ns()
    for token_id, shares in found_balances.items():
        existing = active_by_token.get(token_id)
        if existing is not None:
            if abs((existing.shares or 0.0) - shares) > 1e-6 or existing.state != "OPEN":
                existing.shares = shares
                existing.state = "OPEN"
                existing.pending_exit_order_id = None
                existing.pending_entry_order_id = None
                result.adjusted_existing += 1
                if live_exit_logger:
                    live_exit_logger.log_lifecycle(
                        position=existing,
                        event="startup_reconcile_existing_balance",
                    )
            continue

        # 2026-05-27: skip tokens that were manually closed as permanent orphans.
        # Without this guard, reconcile keeps re-opening them every restart
        # (the chain still shows the shares), causing endless exit-attempt spam.
        manually_blocked = any(
            (p.token_id == token_id
             and p.state == "CLOSED"
             and "orphan" in (p.exit_reason or "").lower())
            for p in store.positions.values()
        )
        if manually_blocked:
            logger.info("[startup_reconcile] skipping permanently-orphaned token=%s shares=%.6f", token_id, shares)
            continue

        meta = token_index.get(token_id)
        if not meta:
            logger.warning("[startup_reconcile] positive balance for unmapped token=%s shares=%.6f", token_id, shares)
            continue

        mid = 0.5
        if book_store:
            book = book_store.get(token_id)
            if book:
                bid = float(book.get("best_bid", 0.5) or 0.5)
                ask = float(book.get("best_ask", 0.5) or 0.5)
                mid = (bid + ask) / 2.0

        pos = LivePosition(
            position_id=f"reconcile_{meta['match_id']}_{token_id}_{now_ns}",
            state="OPEN",
            token_id=token_id,
            opposing_token_id=meta["opposing_token_id"],
            match_id=meta["match_id"],
            market_name=meta["market_name"],
            side=meta["side"],
            entry_price=mid,
            shares=shares,
            cost_usd=shares * mid,
            entry_time_ns=now_ns,
            entry_game_time_sec=None,
            event_type="STARTUP_RECONCILE",
            expected_move=0.0,
            fair_price=mid,
        )
        store.positions[pos.position_id] = pos
        result.reopened_missing += 1
        if live_exit_logger:
            live_exit_logger.log_lifecycle(position=pos, event="startup_reconcile_missing_balance")

    found_tokens = set(found_balances)
    for token_id, pos in active_by_token.items():
        if token_id in found_tokens:
            continue
        pos.state = "CLOSED"
        pos.pending_exit_order_id = None
        pos.pending_entry_order_id = None
        result.closed_stale += 1
        if live_exit_logger:
            live_exit_logger.log_lifecycle(position=pos, event="startup_reconcile_zero_balance")

    store.save()
    result.active_after = len(store.open_positions())
    if live_executor is not None:
        live_executor.open_positions = result.active_after
        live_executor._save()
    return result
