from __future__ import annotations

import time
import pytest

from runtime.bot_runtime import _annotate_signal_policy_for_paper
from execution_policy import PolicyResult

class DummyTrader:
    def __init__(self):
        self.positions = {}
        self._match_open_usd = {}

def test_annotate_signal_policy_modes(monkeypatch):
    # Setup mock data
    signal = {"strategy_kind": "VALUE", "side": "YES", "event_type": "VALUE_HOLD"}
    mapping = {"market_type": "MAP_WINNER"}
    game = {"match_id": "M1", "data_source": "top_live", "received_at_ns": time.time_ns()}
    book_store = {}
    trader = DummyTrader()
    
    # 1. Research mode
    monkeypatch.setattr("runtime.bot_runtime.PAPER_MODE", "research")
    res1 = _annotate_signal_policy_for_paper(
        signal=signal,
        token_id="TOK1",
        side="YES",
        mapping=mapping,
        game=game,
        book_store=book_store,
        trader=trader
    )
    assert "policy_allowed" in res1
    assert "would_pass_live" in res1

    # 2. Live Parity mode
    monkeypatch.setattr("runtime.bot_runtime.PAPER_MODE", "live_parity")
    res2 = _annotate_signal_policy_for_paper(
        signal=signal,
        token_id="TOK1",
        side="YES",
        mapping=mapping,
        game=game,
        book_store=book_store,
        trader=trader
    )
    assert "policy_allowed" in res2

    # 3. Shadow Live mode
    monkeypatch.setattr("runtime.bot_runtime.PAPER_MODE", "shadow_live")
    res3 = _annotate_signal_policy_for_paper(
        signal=signal,
        token_id="TOK1",
        side="YES",
        mapping=mapping,
        game=game,
        book_store=book_store,
        trader=trader
    )
    assert "policy_allowed" in res3

