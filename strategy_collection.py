"""strategy_collection.py — Extract strategy candidate collection from runtime.

This module gathers candidates from various engines (Value, DSwing, EventTriggeredValue)
and packages them into StrategyCandidate objects for the allocator.
It preserves all logging and side effects found in the original runtime.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING

from strategy_allocator import StrategyCandidate
from value_engine import ValueSignal
from decisive_swing_engine import DSwingSignal
from event_triggered_value_engine import EventTriggeredValueSignal
from model_value_engine import ModelValueSignal, ModelValueEngine

if TYPE_CHECKING:
    from value_engine import ValueEngine
    from decisive_swing_engine import DecisiveSwingEngine
    from event_triggered_value_engine import EventTriggeredValueEngine


@dataclass
class StrategyCollectionLoggers:
    """Loggers injected from the runtime to preserve side effects."""
    value_logger: Any | None = None
    dswing_logger: Any | None = None
    strategy_signal_logger: Any | None = None
    # Callback to trigger markout logging: fn(row_dict, token_id)
    markout_logger_fn: Callable[[dict, str], None] | None = None


@dataclass
class StrategyCollectionContext:
    """Context required to evaluate all strategy engines for a (game, mapping) pair."""
    game: dict
    mapping: dict
    book_store: Any
    entered_tokens: set[str]
    live_active_tokens: set[str]

    event_value_engine: EventTriggeredValueEngine | None = None
    value_engine: ValueEngine | None = None
    dswing_engine: DecisiveSwingEngine | None = None
    model_value_engine: ModelValueEngine | None = None

    enable_event_triggered_value_trading: bool = False
    enable_value_trading: bool = False
    dswing_enabled: bool = False
    enable_match_winner_trading: bool = False
    enable_model_value_trading: bool = False

    # fn(ValueSignal) -> (bool, reason)
    value_confirmation_fn: Callable[[ValueSignal], tuple[bool, str]] | None = None
    model_value_confirmation_fn: Callable[[ModelValueSignal], tuple[bool, str]] | None = None
    loggers: StrategyCollectionLoggers = field(default_factory=StrategyCollectionLoggers)

    # Required for EventTriggeredValueEngine
    game_actual_events: list[Any] = field(default_factory=list)
    pre_event_books_by_event: dict[str, dict[str, dict]] = field(default_factory=dict)


def collect_strategy_candidates(ctx: StrategyCollectionContext) -> list[StrategyCandidate]:
    """Gather all eligible strategy candidates for this tick's (game, mapping) pair."""
    candidates: list[StrategyCandidate] = []

    # 1. Event-Triggered Value
    candidates.extend(_collect_event_value_candidates(ctx))

    # 2. Model Value
    candidates.extend(_collect_model_value_candidates(ctx))

    # 3. Value Engine
    candidates.extend(_collect_value_candidates(ctx))

    # 4. Decisive-Swing Engine
    candidates.extend(_collect_dswing_candidates(ctx))

    return candidates


def _collect_event_value_candidates(ctx: StrategyCollectionContext) -> list[StrategyCandidate]:
    if ctx.event_value_engine is None or ctx.loggers.strategy_signal_logger is None:
        return []

    results: list[StrategyCandidate] = []
    for actual_event in ctx.game_actual_events:
        ev_results = ctx.event_value_engine.evaluate(
            event=actual_event,
            game=ctx.game,
            mapping=ctx.mapping,
            book_store=ctx.book_store,
            entered_tokens=ctx.entered_tokens,
            pre_event_books=ctx.pre_event_books_by_event.get(actual_event.event_id),
        )
        for ev_result in ev_results:
            if not isinstance(ev_result, EventTriggeredValueSignal):
                ctx.loggers.strategy_signal_logger.log_reject(ev_result)
                continue

            ctx.loggers.strategy_signal_logger.log_signal(ev_result)

            # Markout logging (async side effect via callback)
            if ctx.loggers.markout_logger_fn is not None:
                ev_book = ctx.book_store.get(ev_result.token_id)
                markout_row = {
                    "signal_timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    "match_id": str(ctx.game.get("match_id") or ""),
                    "market_name": ctx.mapping.get("name"),
                    "event_type": "EVENT_REVERSAL_EDGE" if ev_result.is_reversal else "EVENT_CONTINUATION_EDGE",
                    "event_tier": "A",
                    "event_is_primary": True,
                    "event_direction": ev_result.direction,
                    "token_id": ev_result.token_id,
                    "side": ev_result.side,
                    "decision": "paper_buy_yes",
                    "skip_reason": "",
                    "reference_price": ev_result.ask,
                    "reference_bid": ev_book.get("best_bid") if ev_book else None,
                    "reference_ask": ev_result.ask,
                    "fair_price": ev_result.fair_price,
                    "fair_raw": ev_result.fair_after_raw,
                    "fair_used": ev_result.fair_after_used,
                    "model_available": ev_result.model_available,
                    "model_reason": ev_result.model_reason,
                    "executable_edge": ev_result.edge,
                    "edge_type": ev_result.edge_type,
                    "target_horizon": ev_result.target_horizon,
                    "expected_hold_sec": ev_result.expected_hold_sec,
                    "entry_trigger": ev_result.entry_trigger,
                    "exit_trigger": ev_result.exit_trigger,
                    "primary_metric": ev_result.primary_metric,
                    "secondary_metric": ev_result.secondary_metric,
                    "promotion_rule": ev_result.promotion_rule,
                    "disable_rule": ev_result.disable_rule,
                }
                ctx.loggers.markout_logger_fn(markout_row, ev_result.token_id)

            if not ctx.enable_event_triggered_value_trading:
                continue

            ev_strategy = (
                "EVENT_REVERSAL_EDGE"
                if ev_result.is_reversal
                else "EVENT_CONTINUATION_EDGE"
            )
            results.append(StrategyCandidate(
                strategy=ev_strategy,
                token_id=str(ev_result.token_id),
                match_id=str(ctx.game.get("match_id") or ""),
                direction=ev_result.direction,
                edge=ev_result.edge,
                fair=ev_result.fair_price,
                game_time_sec=ev_result.game_time_sec,
                signal=ev_result,
                edge_type=ev_result.edge_type,
                target_horizon=ev_result.target_horizon,
                expected_hold_sec=ev_result.expected_hold_sec,
                entry_trigger=ev_result.entry_trigger,
                exit_trigger=ev_result.exit_trigger,
                primary_metric=ev_result.primary_metric,
                secondary_metric=ev_result.secondary_metric,
                promotion_rule=ev_result.promotion_rule,
                disable_rule=ev_result.disable_rule,
                is_reversal=ev_result.is_reversal,
                event_subtype=ev_result.actual_event_type,
            ))
    return results


def _collect_value_candidates(ctx: StrategyCollectionContext) -> list[StrategyCandidate]:
    if ctx.value_engine is None or ctx.loggers.value_logger is None:
        return []

    results: list[StrategyCandidate] = []
    value_results = ctx.value_engine.evaluate(ctx.game, ctx.mapping, ctx.book_store, entered_tokens=ctx.entered_tokens)
    for result in value_results:
        if not isinstance(result, ValueSignal):
            if ctx.loggers.value_logger is not None:
                ctx.loggers.value_logger.log_reject(result)
            if ctx.loggers.strategy_signal_logger is not None:
                ctx.loggers.strategy_signal_logger.log_reject(result, strategy="VALUE_EDGE")
            continue

        if ctx.loggers.value_logger is not None:
            ctx.loggers.value_logger.log_signal(result)
        if ctx.loggers.strategy_signal_logger is not None:
            ctx.loggers.strategy_signal_logger.log_signal(result, strategy="VALUE_EDGE")

        if not ctx.enable_value_trading:
            continue

        confirmed = True
        if ctx.value_confirmation_fn is not None:
            confirmed, _ = ctx.value_confirmation_fn(result)

        results.append(StrategyCandidate(
            strategy="VALUE_EDGE",
            token_id=str(result.token_id),
            match_id=str(ctx.game.get("match_id") or ""),
            direction=result.direction,
            edge=result.edge,
            fair=result.fair_price,
            game_time_sec=result.game_time_sec,
            signal=result,
            edge_type=result.edge_type,
            target_horizon=result.target_horizon,
            expected_hold_sec=result.expected_hold_sec,
            entry_trigger=result.entry_trigger,
            exit_trigger=result.exit_trigger,
            primary_metric=result.primary_metric,
            secondary_metric=result.secondary_metric,
            promotion_rule=result.promotion_rule,
            disable_rule=result.disable_rule,
            would_pass_confirmation=confirmed,
        ))
    return results


def _collect_dswing_candidates(ctx: StrategyCollectionContext) -> list[StrategyCandidate]:
    if ctx.dswing_engine is None:
        return []

    results: list[StrategyCandidate] = []
    for ds_res in ctx.dswing_engine.evaluate(ctx.game, ctx.mapping, ctx.book_store):
        if not isinstance(ds_res, DSwingSignal):
            if ctx.loggers.dswing_logger is not None:
                ctx.loggers.dswing_logger.log_reject(ds_res, mapping=ctx.mapping)
            if ctx.loggers.strategy_signal_logger is not None:
                ctx.loggers.strategy_signal_logger.log_reject(ds_res, strategy="DSWING")
            continue

        if ctx.loggers.dswing_logger is not None:
            ctx.loggers.dswing_logger.log_signal(ds_res, mapping=ctx.mapping)
        if ctx.loggers.strategy_signal_logger is not None:
            ctx.loggers.strategy_signal_logger.log_signal(ds_res, strategy="DSWING")

        # Markout logging (async side effect via callback)
        if ctx.loggers.markout_logger_fn is not None:
            ds_book = ctx.book_store.get(ds_res.token_id)
            markout_row = {
                "signal_timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "match_id": str(ctx.game.get("match_id") or ""),
                "market_name": ctx.mapping.get("name"),
                "event_type": "DSWING",
                "event_tier": "A",
                "event_is_primary": True,
                "event_direction": ds_res.direction,
                "token_id": ds_res.token_id,
                "side": ds_res.side,
                "decision": "paper_buy_yes",
                "skip_reason": "",
                "reference_price": ds_res.ask,
                "reference_bid": ds_book.get("best_bid") if ds_book else None,
                "reference_ask": ds_res.ask,
                "fair_price": ds_res.series_fair,
                "executable_edge": ds_res.edge,
                "edge_type": ds_res.edge_type,
                "target_horizon": ds_res.target_horizon,
                "expected_hold_sec": ds_res.expected_hold_sec,
                "entry_trigger": ds_res.entry_trigger,
                "exit_trigger": ds_res.exit_trigger,
                "primary_metric": ds_res.primary_metric,
                "secondary_metric": ds_res.secondary_metric,
                "promotion_rule": ds_res.promotion_rule,
                "disable_rule": ds_res.disable_rule,
            }
            ctx.loggers.markout_logger_fn(markout_row, ds_res.token_id)

        if not ctx.dswing_enabled:
            continue

        # Match Winner Research Mode Check
        is_match_winner = ctx.mapping.get("market_type") == "MATCH_WINNER"
        if is_match_winner and not ctx.enable_match_winner_trading:
            continue

        # DSWING dedup: blocks both sides (prevents holding opposing tokens)
        ds_opp = ctx.mapping["no_token_id"] if ds_res.token_id == ctx.mapping["yes_token_id"] else ctx.mapping["yes_token_id"]
        if str(ds_res.token_id) in ctx.live_active_tokens or (ds_opp and str(ds_opp) in ctx.live_active_tokens):
            continue

        results.append(StrategyCandidate(
            strategy="DSWING",
            token_id=str(ds_res.token_id),
            match_id=str(ctx.game.get("match_id") or ""),
            direction=ds_res.direction,
            edge=ds_res.edge,
            fair=ds_res.series_fair,
            game_time_sec=ds_res.game_time_sec,
            signal=ds_res,
            edge_type=ds_res.edge_type,
            target_horizon=ds_res.target_horizon,
            expected_hold_sec=ds_res.expected_hold_sec,
            entry_trigger=ds_res.entry_trigger,
            exit_trigger=ds_res.exit_trigger,
            primary_metric=ds_res.primary_metric,
            secondary_metric=ds_res.secondary_metric,
            promotion_rule=ds_res.promotion_rule,
            disable_rule=ds_res.disable_rule,
        ))
    return results


def _collect_model_value_candidates(ctx: StrategyCollectionContext) -> list[StrategyCandidate]:
    if ctx.model_value_engine is None:
        return []

    results: list[StrategyCandidate] = []
    model_results = ctx.model_value_engine.evaluate(
        ctx.game, ctx.mapping, ctx.book_store, entered_tokens=ctx.entered_tokens
    )
    for result in model_results:
        if not isinstance(result, ModelValueSignal):
            if ctx.loggers.strategy_signal_logger is not None:
                ctx.loggers.strategy_signal_logger.log_reject(result, strategy="MODEL_VALUE_EDGE")
            continue

        if ctx.loggers.strategy_signal_logger is not None:
            ctx.loggers.strategy_signal_logger.log_signal(result, strategy="MODEL_VALUE_EDGE")

        if not ctx.enable_model_value_trading:
            continue

        confirmed = True
        if ctx.model_value_confirmation_fn is not None:
            confirmed, _ = ctx.model_value_confirmation_fn(result)

        results.append(StrategyCandidate(
            strategy="MODEL_VALUE_EDGE",
            token_id=str(result.token_id),
            match_id=str(ctx.game.get("match_id") or ""),
            direction=result.direction,
            edge=result.edge,
            fair=result.fair_price,
            game_time_sec=result.game_time_sec,
            signal=result,
            edge_type=result.edge_type,
            target_horizon=result.target_horizon,
            expected_hold_sec=result.expected_hold_sec,
            entry_trigger=result.entry_trigger,
            exit_trigger=result.exit_trigger,
            primary_metric=result.primary_metric,
            secondary_metric=result.secondary_metric,
            promotion_rule=result.promotion_rule,
            disable_rule=result.disable_rule,
            would_pass_confirmation=confirmed,
        ))
    return results
