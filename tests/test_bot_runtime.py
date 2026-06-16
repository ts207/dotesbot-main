from __future__ import annotations

import time
import pytest

from runtime.bot_runtime import _annotate_signal_policy_for_paper
from paper_trader import PaperTrader
import storage_v2

@pytest.fixture(autouse=True)
def mock_storage_path(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_state.sqlite")
    monkeypatch.setattr(storage_v2, "DEFAULT_DB_PATH", db_path)
    return db_path

class Store:
    def __init__(self, books):
        self.books = books

    def get(self, token_id):
        return self.books.get(token_id)

def _get_rejection_scenario():
    # Setup mock data that will definitely fail live policy (no ask size, missing mapping fields, etc)
    signal = {
        "strategy_kind": "VALUE", 
        "side": "YES", 
        "event_type": "VALUE",
        "target_size_usd": 25,
        "fair_price": 0.70,
    }
    mapping = {"market_type": "MAP_WINNER"} # Missing yes_token_id etc
    game = {"match_id": "M1", "data_source": "top_live", "received_at_ns": time.time_ns()}
    # Set ask to 0.50, but it will still fail policy due to missing received_at_ns (book_stale)
    book_store = Store({"TOK1": {"best_ask": 0.50, "best_bid": 0.40, "ask_size": 100}})
    trader = PaperTrader()
    return signal, mapping, game, book_store, trader

def test_research_mode_annotates_bypass(monkeypatch):
    monkeypatch.setattr("runtime.bot_runtime.PAPER_MODE", "research")
    monkeypatch.setattr("paper_trader.PAPER_MODE", "research")
    signal, mapping, game, book_store, trader = _get_rejection_scenario()
    
    annotated = _annotate_signal_policy_for_paper(
        signal=signal,
        token_id="TOK1",
        side="YES",
        mapping=mapping,
        game=game,
        book_store=book_store,
        trader=trader
    )
    
    # Should be rejected by policy, but paper_only_bypass should be True due to would_pass_live=False
    assert annotated.get("policy_allowed") is False
    assert annotated.get("paper_only_bypass") is True

    # research mode should let PaperTrader.enter() pass despite policy rejection
    pos, reason = trader.enter(
        signal=annotated,
        token_id="TOK1",
        side="YES",
        book_store=book_store,
        match_id="M1",
        market_name="Test",
        opposing_token_id="TOK2"
    )
    assert pos is not None
    assert reason == "filled"

def test_live_parity_mode_rejects_trader(monkeypatch):
    monkeypatch.setattr("runtime.bot_runtime.PAPER_MODE", "live_parity")
    monkeypatch.setattr("paper_trader.PAPER_MODE", "live_parity")
    signal, mapping, game, book_store, trader = _get_rejection_scenario()
    
    annotated = _annotate_signal_policy_for_paper(
        signal=signal,
        token_id="TOK1",
        side="YES",
        mapping=mapping,
        game=game,
        book_store=book_store,
        trader=trader
    )
    
    # Should be rejected by policy
    assert annotated.get("would_pass_live") is False
    
    # live_parity mode should make PaperTrader.enter() reject
    pos, reason = trader.enter(
        signal=annotated,
        token_id="TOK1",
        side="YES",
        book_store=book_store,
        match_id="M1",
        market_name="Test",
        opposing_token_id="TOK2"
    )
    assert pos is None
    assert "paper_live_parity_reject" in reason

def test_shadow_live_mode_rejects_trader(monkeypatch):
    monkeypatch.setattr("runtime.bot_runtime.PAPER_MODE", "shadow_live")
    monkeypatch.setattr("paper_trader.PAPER_MODE", "shadow_live")
    signal, mapping, game, book_store, trader = _get_rejection_scenario()
    
    annotated = _annotate_signal_policy_for_paper(
        signal=signal,
        token_id="TOK1",
        side="YES",
        mapping=mapping,
        game=game,
        book_store=book_store,
        trader=trader
    )
    
    assert annotated.get("policy_allowed") is False
    
    # shadow_live mode should make PaperTrader.enter() reject
    pos, reason = trader.enter(
        signal=annotated,
        token_id="TOK1",
        side="YES",
        book_store=book_store,
        match_id="M1",
        market_name="Test",
        opposing_token_id="TOK2"
    )
    assert pos is None
    assert "paper_shadow_live_reject" in reason
