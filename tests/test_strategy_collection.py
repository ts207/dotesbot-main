"""Tests for strategy_collection.py."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

from strategy_collection import (
    StrategyCollectionContext,
    StrategyCollectionLoggers,
    collect_strategy_candidates,
)
from value_engine import ValueSignal
from decisive_swing_engine import DSwingSignal
from event_triggered_value_engine import EventTriggeredValueSignal, EventTriggeredValueReject


@dataclass
class FakeBook:
    best_bid: float | None = 0.80
    best_ask: float | None = 0.82
    received_at_ns: int = 1000

    def get(self, key, default=None):
        return getattr(self, key, default)


class FakeBookStore:
    def __init__(self, books: dict):
        self.books = books

    def get(self, token_id):
        return self.books.get(token_id)


@pytest.fixture
def base_ctx():
    game = {"match_id": "m1", "game_time_sec": 900}
    mapping = {
        "yes_token_id": "tok_yes",
        "no_token_id": "tok_no",
        "name": "Team A vs Team B",
    }
    book_store = FakeBookStore({
        "tok_yes": FakeBook(best_bid=0.80, best_ask=0.82),
        "tok_no": FakeBook(best_bid=0.18, best_ask=0.20),
    })
    return StrategyCollectionContext(
        game=game,
        mapping=mapping,
        book_store=book_store,
        entered_tokens=set(),
        live_active_tokens=set(),
        loggers=StrategyCollectionLoggers(
            value_logger=MagicMock(),
            dswing_logger=MagicMock(),
            strategy_signal_logger=MagicMock(),
        ),
    )


def test_collect_value_candidate_when_value_trading_enabled(base_ctx):
    base_ctx.enable_value_trading = True
    base_ctx.value_engine = MagicMock()
    
    sig = MagicMock(spec=ValueSignal)
    sig.token_id = "tok_yes"
    sig.direction = "radiant"
    sig.edge = 0.10
    sig.fair_price = 0.92
    sig.game_time_sec = 900
    sig.edge_type = "value"
    sig.target_horizon = "30s"
    sig.expected_hold_sec = 30
    sig.entry_trigger = "e"
    sig.exit_trigger = "x"
    sig.primary_metric = "p"
    sig.secondary_metric = "s"
    sig.promotion_rule = "pr"
    sig.disable_rule = "dr"
    
    base_ctx.value_engine.evaluate.return_value = [sig]
    
    candidates = collect_strategy_candidates(base_ctx)
    
    assert len(candidates) == 1
    assert candidates[0].strategy == "VALUE_EDGE"
    assert candidates[0].token_id == "tok_yes"
    assert candidates[0].would_pass_confirmation is True
    
    base_ctx.loggers.value_logger.log_signal.assert_called_once_with(sig)
    base_ctx.loggers.strategy_signal_logger.log_signal.assert_called_once_with(sig, strategy="VALUE_EDGE")


def test_value_signal_logged_but_not_collected_when_value_trading_disabled(base_ctx):
    base_ctx.enable_value_trading = False
    base_ctx.value_engine = MagicMock()
    
    sig = MagicMock(spec=ValueSignal)
    sig.token_id = "tok_yes"
    base_ctx.value_engine.evaluate.return_value = [sig]
    
    candidates = collect_strategy_candidates(base_ctx)
    
    assert len(candidates) == 0
    # Still logged
    base_ctx.loggers.value_logger.log_signal.assert_called_once_with(sig)
    base_ctx.loggers.strategy_signal_logger.log_signal.assert_called_once_with(sig, strategy="VALUE_EDGE")


def test_value_reject_logged_and_not_collected(base_ctx):
    base_ctx.enable_value_trading = True
    base_ctx.value_engine = MagicMock()
    
    reject = {"reason": "too_stale"}
    base_ctx.value_engine.evaluate.return_value = [reject]
    
    candidates = collect_strategy_candidates(base_ctx)
    
    assert len(candidates) == 0
    base_ctx.loggers.value_logger.log_reject.assert_called_once_with(reject)
    base_ctx.loggers.strategy_signal_logger.log_reject.assert_called_once_with(reject, strategy="VALUE_EDGE")


def test_value_confirmation_result_attached_to_candidate(base_ctx):
    base_ctx.enable_value_trading = True
    base_ctx.value_engine = MagicMock()
    base_ctx.value_confirmation_fn = MagicMock(return_value=(False, "waiting"))
    
    sig = MagicMock(spec=ValueSignal)
    sig.token_id = "tok_yes"
    sig.direction = "radiant"
    sig.edge = 0.10
    sig.fair_price = 0.92
    sig.game_time_sec = 900
    sig.edge_type = "value"
    sig.target_horizon = "30s"
    sig.expected_hold_sec = 30
    sig.entry_trigger = "e"
    sig.exit_trigger = "x"
    sig.primary_metric = "p"
    sig.secondary_metric = "s"
    sig.promotion_rule = "pr"
    sig.disable_rule = "dr"

    base_ctx.value_engine.evaluate.return_value = [sig]
    
    candidates = collect_strategy_candidates(base_ctx)
    
    assert len(candidates) == 1
    assert candidates[0].would_pass_confirmation is False
    base_ctx.value_confirmation_fn.assert_called_once_with(sig)


def test_collect_event_continuation_candidate(base_ctx):
    base_ctx.enable_event_triggered_value_trading = True
    base_ctx.event_value_engine = MagicMock()
    
    event = MagicMock()
    event.event_id = "e1"
    base_ctx.game_actual_events = [event]
    
    sig = MagicMock(spec=EventTriggeredValueSignal)
    sig.token_id = "tok_yes"
    sig.is_reversal = False
    sig.direction = "radiant"
    sig.edge = 0.15
    sig.fair_price = 0.97
    sig.game_time_sec = 900
    sig.actual_event_type = "kill"
    sig.edge_type = "event"
    sig.target_horizon = "30s"
    sig.expected_hold_sec = 30
    sig.entry_trigger = "e"
    sig.exit_trigger = "x"
    sig.primary_metric = "p"
    sig.secondary_metric = "s"
    sig.promotion_rule = "pr"
    sig.disable_rule = "dr"

    base_ctx.event_value_engine.evaluate.return_value = [sig]
    
    candidates = collect_strategy_candidates(base_ctx)
    
    assert len(candidates) == 1
    assert candidates[0].strategy == "EVENT_CONTINUATION_EDGE"
    assert candidates[0].is_reversal is False
    assert candidates[0].event_subtype == "kill"
    base_ctx.loggers.strategy_signal_logger.log_signal.assert_called_once_with(sig)


def test_collect_event_reversal_candidate(base_ctx):
    base_ctx.enable_event_triggered_value_trading = True
    base_ctx.event_value_engine = MagicMock()
    
    event = MagicMock()
    event.event_id = "e1"
    base_ctx.game_actual_events = [event]
    
    sig = MagicMock(spec=EventTriggeredValueSignal)
    sig.token_id = "tok_no"
    sig.is_reversal = True
    sig.direction = "dire"
    sig.edge = 0.20
    sig.fair_price = 0.40
    sig.game_time_sec = 900
    sig.actual_event_type = "kill"
    sig.edge_type = "event_rev"
    sig.target_horizon = "30s"
    sig.expected_hold_sec = 30
    sig.entry_trigger = "e"
    sig.exit_trigger = "x"
    sig.primary_metric = "p"
    sig.secondary_metric = "s"
    sig.promotion_rule = "pr"
    sig.disable_rule = "dr"

    base_ctx.event_value_engine.evaluate.return_value = [sig]
    
    candidates = collect_strategy_candidates(base_ctx)
    
    assert len(candidates) == 1
    assert candidates[0].strategy == "EVENT_REVERSAL_EDGE"
    assert candidates[0].is_reversal is True


def test_collect_dswing_candidate_when_enabled(base_ctx):
    base_ctx.dswing_enabled = True
    base_ctx.dswing_engine = MagicMock()
    
    sig = MagicMock(spec=DSwingSignal)
    sig.token_id = "tok_yes"
    sig.direction = "radiant"
    sig.edge = 0.05
    sig.series_fair = 0.87
    sig.game_time_sec = 900
    sig.edge_type = "dswing"
    sig.target_horizon = "end"
    sig.expected_hold_sec = 600
    sig.entry_trigger = "e"
    sig.exit_trigger = "x"
    sig.primary_metric = "p"
    sig.secondary_metric = "s"
    sig.promotion_rule = "pr"
    sig.disable_rule = "dr"

    base_ctx.dswing_engine.evaluate.return_value = [sig]
    
    candidates = collect_strategy_candidates(base_ctx)
    
    assert len(candidates) == 1
    assert candidates[0].strategy == "DSWING"
    base_ctx.loggers.dswing_logger.log_signal.assert_called_once_with(sig, mapping=base_ctx.mapping)



def test_dswing_blocks_when_target_token_active(base_ctx):
    base_ctx.dswing_enabled = True
    base_ctx.dswing_engine = MagicMock()
    base_ctx.live_active_tokens = {"tok_yes"}
    
    sig = MagicMock(spec=DSwingSignal)
    sig.token_id = "tok_yes"
    base_ctx.dswing_engine.evaluate.return_value = [sig]
    
    candidates = collect_strategy_candidates(base_ctx)
    
    assert len(candidates) == 0
    # Still logged
    base_ctx.loggers.dswing_logger.log_signal.assert_called_once_with(sig, mapping=base_ctx.mapping)


def test_dswing_blocks_when_opposing_token_active(base_ctx):
    base_ctx.dswing_enabled = True
    base_ctx.dswing_engine = MagicMock()
    base_ctx.live_active_tokens = {"tok_no"} # tok_no is opposing tok_yes
    
    sig = MagicMock(spec=DSwingSignal)
    sig.token_id = "tok_yes"
    base_ctx.dswing_engine.evaluate.return_value = [sig]
    
    candidates = collect_strategy_candidates(base_ctx)
    
    assert len(candidates) == 0


def test_dswing_collects_even_when_value_engine_absent(base_ctx):
    base_ctx.dswing_enabled = True
    base_ctx.dswing_engine = MagicMock()
    base_ctx.value_engine = None
    base_ctx.loggers.value_logger = None
    
    sig = MagicMock(spec=DSwingSignal)
    sig.token_id = "tok_yes"
    sig.direction = "radiant"
    sig.edge = 0.05
    sig.series_fair = 0.87
    sig.game_time_sec = 900
    sig.edge_type = "dswing"
    sig.target_horizon = "end"
    sig.expected_hold_sec = 600
    sig.entry_trigger = "e"
    sig.exit_trigger = "x"
    sig.primary_metric = "p"
    sig.secondary_metric = "s"
    sig.promotion_rule = "pr"
    sig.disable_rule = "dr"

    base_ctx.dswing_engine.evaluate.return_value = [sig]
    
    candidates = collect_strategy_candidates(base_ctx)
    assert len(candidates) == 1
    assert candidates[0].strategy == "DSWING"

def test_dswing_markout_logger_receives_dswing_signal_fields(base_ctx):
    base_ctx.dswing_enabled = True
    base_ctx.enable_match_winner_trading = True
    base_ctx.dswing_engine = MagicMock()
    base_ctx.loggers.markout_logger_fn = MagicMock()
    
    sig = MagicMock(spec=DSwingSignal)
    sig.token_id = "tok_yes"
    sig.direction = "radiant"
    sig.edge = 0.05
    sig.series_fair = 0.87
    sig.game_time_sec = 900
    sig.edge_type = "dswing"
    sig.target_horizon = "end"
    sig.expected_hold_sec = 600
    sig.entry_trigger = "e"
    sig.exit_trigger = "x"
    sig.primary_metric = "p"
    sig.secondary_metric = "s"
    sig.promotion_rule = "pr"
    sig.disable_rule = "dr"
    sig.ask = 0.82
    sig.side = "YES"

    base_ctx.dswing_engine.evaluate.return_value = [sig]
    candidates = collect_strategy_candidates(base_ctx)
    
    assert len(candidates) == 1
    base_ctx.loggers.markout_logger_fn.assert_called_once()
    called_args = base_ctx.loggers.markout_logger_fn.call_args[0]
    markout_row = called_args[0]
    assert markout_row["event_type"] == "DSWING"
    assert markout_row["executable_edge"] == 0.05
    assert markout_row["edge_type"] == "dswing"

def test_match_winner_research_enabled_does_not_execute_when_trading_disabled(base_ctx):
    base_ctx.dswing_enabled = True
    base_ctx.enable_match_winner_trading = False
    base_ctx.mapping["market_type"] = "MATCH_WINNER"
    base_ctx.dswing_engine = MagicMock()
    base_ctx.loggers.markout_logger_fn = MagicMock()
    
    sig = MagicMock(spec=DSwingSignal)
    sig.token_id = "tok_yes"
    sig.direction = "radiant"
    sig.edge = 0.05
    sig.series_fair = 0.87
    sig.game_time_sec = 900
    sig.edge_type = "dswing"
    sig.target_horizon = "end"
    sig.expected_hold_sec = 600
    sig.entry_trigger = "e"
    sig.exit_trigger = "x"
    sig.primary_metric = "p"
    sig.secondary_metric = "s"
    sig.promotion_rule = "pr"
    sig.disable_rule = "dr"
    sig.ask = 0.82
    sig.side = "YES"

    base_ctx.dswing_engine.evaluate.return_value = [sig]
    candidates = collect_strategy_candidates(base_ctx)
    
    assert len(candidates) == 0
    base_ctx.loggers.markout_logger_fn.assert_called_once()

from decisive_swing_engine import DSwingReject

def test_dswing_research_rejects_are_logged(base_ctx):
    base_ctx.dswing_enabled = True
    base_ctx.dswing_engine = MagicMock()
    base_ctx.loggers.strategy_signal_logger = MagicMock()
    rej = DSwingReject(match_id="123", reason="lead_too_small")
    base_ctx.dswing_engine.evaluate.return_value = [rej]
    
    candidates = collect_strategy_candidates(base_ctx)
    assert len(candidates) == 0
    base_ctx.loggers.strategy_signal_logger.log_reject.assert_called_once_with(rej, strategy="DSWING")

def test_dswing_research_rejects_do_not_create_candidates_when_trading_disabled(base_ctx):
    base_ctx.dswing_enabled = True
    base_ctx.enable_match_winner_trading = False
    base_ctx.dswing_engine = MagicMock()
    rej = DSwingReject(match_id="123", reason="research_disabled")
    base_ctx.dswing_engine.evaluate.return_value = [rej]
    
    candidates = collect_strategy_candidates(base_ctx)
    assert len(candidates) == 0

def test_dswing_candidate_created_only_when_match_winner_trading_enabled(base_ctx):
    base_ctx.dswing_enabled = True
    base_ctx.enable_match_winner_trading = True
    base_ctx.mapping["market_type"] = "MATCH_WINNER"
    base_ctx.dswing_engine = MagicMock()
    sig = MagicMock(spec=DSwingSignal)
    sig.token_id = "tok_yes"
    sig.direction = "radiant"
    sig.edge = 0.05
    sig.series_fair = 0.87
    sig.game_time_sec = 900
    sig.edge_type = "dswing"
    sig.target_horizon = "end"
    sig.expected_hold_sec = 600
    sig.entry_trigger = "e"
    sig.exit_trigger = "x"
    sig.primary_metric = "p"
    sig.secondary_metric = "s"
    sig.promotion_rule = "pr"
    sig.disable_rule = "dr"
    sig.ask = 0.82
    sig.side = "YES"
    base_ctx.dswing_engine.evaluate.return_value = [sig]
    
    candidates = collect_strategy_candidates(base_ctx)
    assert len(candidates) == 1
    assert candidates[0].strategy == "DSWING"
