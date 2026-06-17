"""strategy_execution.py — Extract allocated strategy execution from runtime.

This module handles the execution of winners chosen by the allocator.
It dispatches to live executors or paper traders and manages position state.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from strategy_allocator import AllocationDecision
from live_position_store import LivePosition


@dataclass
class StrategyExecutionLoggers:
    """Loggers injected from the runtime to preserve side effects."""
    live_logger: Any | None = None
    position_logger: Any | None = None
    value_logger: Any | None = None
    dswing_logger: Any | None = None
    signal_markout_logger: Any | None = None


@dataclass
class StrategyExecutionContext:
    """Context required to execute strategy decisions."""
    game: dict
    mapping: dict
    book_store: Any

    trader: Any
    live_executor: Any | None = None
    live_position_store: Any | None = None

    entered_tokens: set[str] = field(default_factory=set)
    loggers: StrategyExecutionLoggers = field(default_factory=StrategyExecutionLoggers)

    # Injected helpers to avoid circular imports with runtime
    normalized_entry_fill_fn: Callable | None = None
    annotate_signal_policy_for_paper_fn: Callable | None = None
    
    # Configuration
    enable_real_live_trading: bool = False


@dataclass
class StrategyExecutionResult:
    """Result of an execution attempt for a strategy candidate."""
    strategy: str
    token_id: str
    match_id: str
    status: str
    mode: str = ""
    reason: str = ""
    order_id: str | None = None
    position_id: str | None = None


async def execute_allocation_decisions(
    decisions: list[AllocationDecision],
    ctx: StrategyExecutionContext,
) -> list[StrategyExecutionResult]:
    """Execute all winning candidates in the given allocation decisions."""
    results: list[StrategyExecutionResult] = []

    for dec in decisions:
        if dec.winner is None:
            continue
        
        cand = dec.winner
        strategy = cand.strategy

        if strategy in ("EVENT_CONTINUATION_EDGE", "EVENT_REVERSAL_EDGE"):
            res = await _execute_event_value_winner(dec, ctx)
            if res:
                results.append(res)
        elif strategy == "VALUE_EDGE":
            res = await _execute_value_winner(dec, ctx)
            if res:
                results.append(res)
        elif strategy == "DSWING":
            res = await _execute_dswing_winner(dec, ctx)
            if res:
                results.append(res)

    return results


async def _execute_event_value_winner(
    dec: AllocationDecision,
    ctx: StrategyExecutionContext,
) -> StrategyExecutionResult | None:
    cand = dec.winner
    sig = cand.signal
    mapping = ctx.mapping
    game = ctx.game
    book_store = ctx.book_store

    opposing_tok = mapping["no_token_id"] if sig.token_id == mapping["yes_token_id"] else mapping["yes_token_id"]

    if ctx.live_executor is not None and ctx.live_position_store is not None:
        ev_attempt = await ctx.live_executor.try_buy_value(
            signal=sig,
            mapping=mapping,
            game=game,
            book_store=book_store,
        )
        if ctx.loggers.live_logger is not None:
            ctx.loggers.live_logger.log_attempt(ev_attempt, phase="event_value_entry")

        landed = (
            ev_attempt.filled_size_usd > 0
            or ev_attempt.order_status in ("delayed", "live", "matched", "filled")
        )
        if landed:
            entry_px = ev_attempt.avg_fill_price or ev_attempt.price_cap or sig.ask
            fill = None
            if ctx.normalized_entry_fill_fn:
                fill = ctx.normalized_entry_fill_fn(
                    filled_usd=ev_attempt.filled_size_usd,
                    filled_shares=None,
                    avg_fill_price=ev_attempt.avg_fill_price,
                    fallback_price=entry_px,
                )
            
            is_filled = fill is not None and ev_attempt.filled_size_usd > 0
            if fill:
                cost, shares, entry_px = fill
            else:
                cost = ev_attempt.submitted_size_usd or 0.0
                shares = 0.0

            ev_strategy_kind = "EVENT_REVERSAL_EDGE" if sig.is_reversal else "EVENT_CONTINUATION_EDGE"
            ev_hold_policy = "reversal_bounce_or_thesis" if sig.is_reversal else "thesis_invalidation"
            ev_exit_engine = "event_reversal_exit" if sig.is_reversal else "value_fair_invalidation"

            pos = LivePosition(
                position_id=f"{ev_attempt.match_id}:{ev_attempt.token_id}:{ev_attempt.created_at_ns}",
                state="OPEN" if is_filled else "PENDING_ENTRY",
                token_id=ev_attempt.token_id,
                opposing_token_id=opposing_tok or "",
                match_id=ev_attempt.match_id,
                market_name=mapping.get("name"),
                side=sig.side,
                entry_price=entry_px,
                shares=shares,
                cost_usd=cost,
                entry_time_ns=ev_attempt.created_at_ns,
                entry_game_time_sec=sig.game_time_sec,
                event_type=ev_strategy_kind,
                expected_move=0.0,
                fair_price=sig.fair_price,
                trader_kind="value",
                exit_horizon_sec=None,
                signal_id=sig.signal_id,
                backed_direction=sig.direction,
                pending_entry_order_id=ev_attempt.order_id if not is_filled else None,
                strategy_kind=ev_strategy_kind,
                strategy_family="EVENT",
                strategy_subtype=sig.actual_event_type,
                entry_is_reversal=sig.is_reversal,
                entry_is_continuation=sig.is_continuation,
                entry_engine="event_triggered_value",
                exit_engine=ev_exit_engine,
                hold_policy=ev_hold_policy,
                edge_type=sig.edge_type,
                target_horizon=sig.target_horizon,
                expected_hold_sec=sig.expected_hold_sec,
                entry_trigger=sig.entry_trigger,
                exit_trigger=sig.exit_trigger,
                primary_metric=sig.primary_metric,
                secondary_metric=sig.secondary_metric,
                promotion_rule=sig.promotion_rule,
                disable_rule=sig.disable_rule,
                entry_fair=sig.fair_price,
                entry_edge=sig.edge,
                entry_ask=sig.ask,
                entry_backed_side=sig.direction,
                entry_radiant_lead=sig.lead,
                entry_actual_event_type=sig.actual_event_type,
                entry_derived_state_flags=list(sig.derived_state_flags),
            )
            ctx.live_position_store.add(pos)
            ctx.entered_tokens.add(str(sig.token_id))
            print(
                f"EVENT_VALUE ENTER {mapping.get('name')} {sig.side} "
                f"event={sig.actual_event_type} entry≈{entry_px:.4f} edge={sig.edge:.4f} status={ev_attempt.order_status}"
            )
            return StrategyExecutionResult(
                strategy=cand.strategy,
                token_id=str(sig.token_id),
                match_id=str(game.get("match_id") or ""),
                status="entered",
                mode="LIVE",
                order_id=ev_attempt.order_id,
                position_id=pos.position_id,
            )
        else:
            print(f"EVENT_VALUE REJECT {mapping.get('name')} {sig.side} reason={ev_attempt.reason_if_rejected}")
            return StrategyExecutionResult(
                strategy=cand.strategy,
                token_id=str(sig.token_id),
                match_id=str(game.get("match_id") or ""),
                status="rejected",
                mode="LIVE",
                reason=ev_attempt.reason_if_rejected or "",
            )
    else:
        # Paper path
        annotated_signal = sig.to_signal_dict()
        if ctx.annotate_signal_policy_for_paper_fn:
            annotated_signal = ctx.annotate_signal_policy_for_paper_fn(
                signal=annotated_signal,
                token_id=sig.token_id,
                side=sig.side,
                mapping=mapping,
                game={"match_id": str(game.get("match_id") or "")},
                book_store=book_store,
                trader=ctx.trader,
            )
        pos, reason = ctx.trader.enter(
            signal=annotated_signal,
            token_id=sig.token_id,
            side=sig.side,
            book_store=book_store,
            match_id=str(game.get("match_id") or ""),
            market_name=mapping.get("name"),
            opposing_token_id=opposing_tok,
        )
        if pos:
            if ctx.loggers.position_logger:
                ctx.loggers.position_logger.log_entry(pos)
            print(f"EVENT_VALUE ENTER {mapping.get('name')} {sig.side} event={sig.actual_event_type} price={pos.entry_price:.4f} edge={sig.edge:.4f}")
            return StrategyExecutionResult(
                strategy=cand.strategy,
                token_id=str(sig.token_id),
                match_id=str(game.get("match_id") or ""),
                status="entered",
                mode="PAPER",
                position_id=pos.position_id,
            )
    return None


async def _execute_value_winner(
    dec: AllocationDecision,
    ctx: StrategyExecutionContext,
) -> StrategyExecutionResult | None:
    cand = dec.winner
    sig = cand.signal
    mapping = ctx.mapping
    game = ctx.game
    book_store = ctx.book_store

    # Confirmation gate
    if not cand.would_pass_confirmation:
        print(
            f"VALUE LIVE WAIT {mapping.get('name')} {sig.side} "
            f"edge={sig.edge:.4f} ask={sig.ask:.4f}"
        )
        return StrategyExecutionResult(
            strategy="VALUE_EDGE",
            token_id=str(sig.token_id),
            match_id=str(game.get("match_id") or ""),
            status="waiting_confirmation",
        )

    opposing_tok = mapping["no_token_id"] if sig.token_id == mapping["yes_token_id"] else mapping["yes_token_id"]

    if ctx.live_executor is not None and ctx.live_position_store is not None:
        v_attempt = await ctx.live_executor.try_buy_value(
            signal=sig, mapping=mapping, game=game, book_store=book_store)
        if ctx.loggers.live_logger is not None:
            ctx.loggers.live_logger.log_attempt(v_attempt, phase="entry")
        
        landed = (v_attempt.filled_size_usd > 0
                  or v_attempt.order_status in ("delayed", "live", "matched", "filled"))
        if landed:
            entry_px = v_attempt.avg_fill_price or v_attempt.price_cap or sig.ask
            fill = None
            if ctx.normalized_entry_fill_fn:
                fill = ctx.normalized_entry_fill_fn(
                    filled_usd=v_attempt.filled_size_usd,
                    filled_shares=None,
                    avg_fill_price=v_attempt.avg_fill_price,
                    fallback_price=entry_px,
                )
            
            is_filled = fill is not None and v_attempt.filled_size_usd > 0
            if fill:
                cost, v_shares, entry_px = fill
            else:
                cost = v_attempt.submitted_size_usd or 0.0
                v_shares = 0.0
            
            v_pos = LivePosition(
                position_id=f"{v_attempt.match_id}:{v_attempt.token_id}:{v_attempt.created_at_ns}",
                state="OPEN" if is_filled else "PENDING_ENTRY",
                token_id=v_attempt.token_id,
                opposing_token_id=opposing_tok or "",
                match_id=v_attempt.match_id,
                market_name=mapping.get("name"),
                side=sig.side,
                entry_price=entry_px,
                shares=v_shares,
                cost_usd=cost,
                entry_time_ns=v_attempt.created_at_ns,
                entry_game_time_sec=sig.game_time_sec,
                event_type="VALUE_EDGE",
                expected_move=0.0,
                fair_price=sig.fair_price,
                trader_kind="value",
                exit_horizon_sec=None,
                signal_id=sig.signal_id,
                backed_direction=sig.direction,
                pending_entry_order_id=v_attempt.order_id if not is_filled else None,
                strategy_kind="VALUE_EDGE",
                strategy_family="VALUE",
                entry_engine="value",
                exit_engine="value_fair_invalidation",
                hold_policy="thesis_invalidation",
                edge_type=sig.edge_type,
                target_horizon=sig.target_horizon,
                expected_hold_sec=sig.expected_hold_sec,
                entry_trigger=sig.entry_trigger,
                exit_trigger=sig.exit_trigger,
                primary_metric=sig.primary_metric,
                secondary_metric=sig.secondary_metric,
                promotion_rule=sig.promotion_rule,
                disable_rule=sig.disable_rule,
                entry_fair=sig.fair_price,
                entry_edge=sig.edge,
                entry_ask=sig.ask,
                entry_backed_side=sig.direction,
                entry_radiant_lead=sig.lead,
            )
            ctx.live_position_store.add(v_pos)
            ctx.entered_tokens.add(str(sig.token_id))
            print(f"VALUE LIVE ENTER {mapping.get('name')} {sig.side} entry≈{entry_px:.4f} edge={sig.edge:.4f} status={v_attempt.order_status}")
            return StrategyExecutionResult(
                strategy="VALUE_EDGE",
                token_id=str(sig.token_id),
                match_id=str(game.get("match_id") or ""),
                status="entered",
                mode="LIVE",
                order_id=v_attempt.order_id,
                position_id=v_pos.position_id,
            )
        else:
            print(f"VALUE LIVE REJECT {mapping.get('name')} {sig.side} reason={v_attempt.reason_if_rejected}")
            return StrategyExecutionResult(
                strategy="VALUE_EDGE",
                token_id=str(sig.token_id),
                match_id=str(game.get("match_id") or ""),
                status="rejected",
                mode="LIVE",
                reason=v_attempt.reason_if_rejected or "",
            )
    else:
        # Paper path
        annotated_signal = sig.to_signal_dict()
        if ctx.annotate_signal_policy_for_paper_fn:
            annotated_signal = ctx.annotate_signal_policy_for_paper_fn(
                signal=annotated_signal,
                token_id=sig.token_id,
                side=sig.side,
                mapping=mapping,
                game={"match_id": str(game.get("match_id") or "")},
                book_store=book_store,
                trader=ctx.trader,
            )
        pos, reason = ctx.trader.enter(
            signal=annotated_signal,
            token_id=sig.token_id,
            side=sig.side,
            book_store=book_store,
            match_id=str(game.get("match_id") or ""),
            market_name=mapping.get("name"),
            opposing_token_id=opposing_tok,
        )
        if pos:
            if ctx.loggers.position_logger:
                ctx.loggers.position_logger.log_entry(pos)
            print(f"VALUE ENTER {mapping.get('name')} {sig.side} price={pos.entry_price:.4f} edge={sig.edge:.4f}")
            return StrategyExecutionResult(
                strategy="VALUE_EDGE",
                token_id=str(sig.token_id),
                match_id=str(game.get("match_id") or ""),
                status="entered",
                mode="PAPER",
                position_id=pos.position_id,
            )
    return None


async def _execute_dswing_winner(
    dec: AllocationDecision,
    ctx: StrategyExecutionContext,
) -> StrategyExecutionResult | None:
    cand = dec.winner
    sig = cand.signal
    mapping = ctx.mapping
    game = ctx.game
    book_store = ctx.book_store

    ds_opp = mapping["no_token_id"] if sig.token_id == mapping["yes_token_id"] else mapping["yes_token_id"]

    if ctx.live_executor is not None and ctx.live_position_store is not None:
        a = await ctx.live_executor.try_buy_value(signal=sig, mapping=mapping, game=game, book_store=book_store)
        if ctx.loggers.live_logger is not None:
            ctx.loggers.live_logger.log_attempt(a, phase="dswing_entry")
        
        if a.filled_size_usd > 0 or a.order_status in ("delayed", "live", "matched", "filled"):
            epx = a.avg_fill_price or a.price_cap or sig.ask
            cost = a.filled_size_usd or a.submitted_size_usd or 0.0
            
            ds_pos = LivePosition(
                position_id=f"{a.match_id}:{a.token_id}:{a.created_at_ns}",
                state="OPEN" if a.order_status in ("matched", "filled") else "PENDING_ENTRY",
                token_id=a.token_id,
                opposing_token_id=ds_opp or "",
                match_id=a.match_id,
                market_name=mapping.get("name"),
                side=sig.side,
                entry_price=epx,
                shares=(cost / epx if epx else 0.0),
                cost_usd=cost,
                entry_time_ns=a.created_at_ns,
                entry_game_time_sec=sig.game_time_sec,
                event_type="DSWING",
                expected_move=0.0,
                fair_price=sig.series_fair,
                trader_kind="dswing",
                exit_horizon_sec=None,
                signal_id=sig.signal_id,
                backed_direction=sig.direction,
                strategy_kind="DSWING",
                strategy_family="DSWING",
                strategy_subtype=None,
                entry_is_reversal=False,
                entry_is_continuation=False,
                entry_engine="decisive_swing",
                exit_engine="dswing_map_end",
                hold_policy="map_end_convergence",
                edge_type=sig.edge_type,
                target_horizon=sig.target_horizon,
                expected_hold_sec=sig.expected_hold_sec,
                entry_trigger=sig.entry_trigger,
                exit_trigger=sig.exit_trigger,
                primary_metric=sig.primary_metric,
                secondary_metric=sig.secondary_metric,
                promotion_rule=sig.promotion_rule,
                disable_rule=sig.disable_rule,
                entry_fair=sig.series_fair,
                entry_edge=sig.edge,
                entry_ask=sig.ask,
                entry_backed_side=sig.direction,
                entry_radiant_lead=sig.lead,
                entry_p_game=sig.p_game,
                entry_series_fair=sig.series_fair,
                entry_series_score_yes=mapping.get("series_score_yes"),
                entry_series_score_no=mapping.get("series_score_no"),
                entry_current_game_number=mapping.get("current_game_number") or mapping.get("game_number"),
                entry_market_type=mapping.get("market_type"),
                entry_book_age_ms=sig.book_age_ms,
                pending_entry_order_id=a.order_id if a.order_status not in ("matched", "filled") else None,
            )
            ctx.live_position_store.add(ds_pos)
            ctx.entered_tokens.add(str(sig.token_id))
            mode_str = "LIVE" if ctx.enable_real_live_trading else "PAPER"
            print(f"DSWING {mode_str} ENTER {mapping.get('name')} {sig.side} ask={sig.ask:.3f} fair={sig.series_fair:.3f} edge={sig.edge:+.3f} status={a.order_status}")
            return StrategyExecutionResult(
                strategy="DSWING",
                token_id=str(sig.token_id),
                match_id=str(game.get("match_id") or ""),
                status="entered",
                mode=mode_str,
                order_id=a.order_id,
                position_id=ds_pos.position_id,
            )
        else:
            print(f"DSWING REJECT {mapping.get('name')} {sig.side} reason={a.reason_if_rejected}")
            return StrategyExecutionResult(
                strategy="DSWING",
                token_id=str(sig.token_id),
                match_id=str(game.get("match_id") or ""),
                status="rejected",
                mode="LIVE",
                reason=a.reason_if_rejected or "",
            )
    else:
        # Paper path
        ds_signal_dict = {
            "signal_id": sig.signal_id,
            "match_id": sig.match_id,
            "decision": "paper_buy_yes",
            "reason": "dswing_edge",
            "token_id": sig.token_id,
            "side": sig.side,
            "fair_price": sig.series_fair,
            "executable_edge": sig.edge,
            "expected_move": 0.0,
            "target_size_usd": sig.sized_usd,
            "size_multiplier": 1.0,
            "event_type": "DSWING",
            "event_tier": "A",
            "event_is_primary": True,
            "event_family": "DSWING",
            "event_quality": 1.0,
            "event_direction": sig.direction,
            "strategy_kind": "DSWING",
            "strategy_family": "DSWING",
            "entry_engine": "decisive_swing",
            "exit_engine": "dswing_map_end",
            "hold_policy": "map_end_convergence",
            "edge_type": sig.edge_type,
            "target_horizon": sig.target_horizon,
            "expected_hold_sec": sig.expected_hold_sec,
            "entry_trigger": sig.entry_trigger,
            "exit_trigger": sig.exit_trigger,
            "primary_metric": sig.primary_metric,
            "secondary_metric": sig.secondary_metric,
            "promotion_rule": sig.promotion_rule,
            "disable_rule": sig.disable_rule,
            "p_game_used": sig.p_game_used,
            "entry_is_reversal": False,
            "entry_is_continuation": False,
            "would_pass_live_gates": sig.would_pass_live_gates,
            "would_pass_live": sig.would_pass_live,
            "live_skip_reason": sig.live_skip_reason,
            "paper_only_bypass": sig.paper_only_bypass,
            "policy_allowed": sig.policy_allowed,
            "policy_reason": sig.policy_reason,
            "policy_version": sig.policy_version,
            "risk_tags": sig.risk_tags,
            "max_fill_price": sig.ask,
        }
        annotated_signal = ds_signal_dict
        if ctx.annotate_signal_policy_for_paper_fn:
            annotated_signal = ctx.annotate_signal_policy_for_paper_fn(
                signal=ds_signal_dict,
                token_id=sig.token_id,
                side=sig.side,
                mapping=mapping,
                game=game,
                book_store=book_store,
                trader=ctx.trader,
            )
        pos, reason = ctx.trader.enter(
            signal=annotated_signal,
            token_id=sig.token_id,
            side=sig.side,
            book_store=book_store,
            match_id=str(game.get("match_id") or ""),
            market_name=mapping.get("name"),
            opposing_token_id=ds_opp,
        )
        if pos:
            if ctx.loggers.position_logger:
                ctx.loggers.position_logger.log_entry(pos)
            print(f"DSWING PAPER ENTER {mapping.get('name')} {sig.side} price={pos.entry_price:.4f} edge={sig.edge:+.3f}")
            return StrategyExecutionResult(
                strategy="DSWING",
                token_id=str(sig.token_id),
                match_id=str(game.get("match_id") or ""),
                status="entered",
                mode="PAPER",
                position_id=pos.position_id,
            )
    return None
