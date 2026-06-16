"""Tests for LiveExecutor.try_buy_continuous (Phase CS-3)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from continuous_scorer import ContinuousSignal
from live_executor import LiveExecutor, LiveOrderAttempt


class FakeClient:
    def __init__(self):
        self.calls = []

    async def buy_fak_market(self, **kwargs):
        self.calls.append(kwargs)
        return {"success": True, "status": "matched",
                "avgFillPrice": kwargs["price_cap"]}


def _sig(side="YES", direction=1, sized_usd=7.5):
    return ContinuousSignal(
        signal_id="sig-1", match_id="12345",
        received_at_ns=time.time_ns(),
        direction=direction, side=side,
        sized_usd=sized_usd, exit_horizon_sec=60,
        yes_mid=0.55, no_mid=0.45,
        yes_ask=0.56, no_ask=0.46,
        yes_bid=0.54, no_bid=0.44,
        ref_mid_blended=0.55, game_time_sec=1820,
        d_lead_1=2000, d_kill_1=2, cur_lead_yes=2500,
        pregame_signed=0.12,
        book_imbalance_yes=0.4, book_imbalance_no=0.5,
        snap_gap_sec=20.0,
        conviction_mult=1.5, magnitude_mult=1.5,
    )


def _mapping(yes="YES_TOK", no="NO_TOK"):
    return {"name": "M1", "yes_token_id": yes, "no_token_id": no,
            "tick_size": "0.01", "neg_risk": False}


def _game(match_id="12345"):
    return {"match_id": match_id, "game_time_sec": 1820, "received_at_ns": time.time_ns()}


class FakeBookStore:
    def __init__(self, books=None):
        self._books = books or {}
    def get(self, asset_id):
        return self._books.get(asset_id)
    def set(self, asset_id, *, bid, ask):
        self._books[asset_id] = {"best_bid": bid, "best_ask": ask,
                                  "ask_size": 100, "bid_size": 100,
                                  "received_at_ns": time.time_ns()}


@pytest.fixture
def executor(monkeypatch):
    monkeypatch.setattr("live_executor.ENABLE_REAL_LIVE_TRADING", True)
    monkeypatch.setattr("live_executor.MAX_TRADE_USD", 50.0)
    monkeypatch.setattr("live_executor.MAX_TOTAL_LIVE_USD", 1000.0)
    monkeypatch.setattr("live_executor.MAX_OPEN_POSITIONS", 10)
    monkeypatch.setattr("live_executor.MAX_DAILY_DRAWDOWN_USD", 70.0)
    monkeypatch.setattr("live_executor.load_live_state",
                        lambda: {"total_submitted_usd": 0.0, "total_filled_usd": 0.0,
                                  "open_positions": 0, "daily_realized_pnl_usd": 0.0,
                                  "submitted_match_sides": {}, "submitted_match_usd": {}})
    monkeypatch.setattr("live_executor.save_live_state", lambda *a, **kw: None)
    e = LiveExecutor(client=FakeClient())
    # Override the disk_guard so it doesn't reject in tests
    e.disk_guard.reject_reason = lambda: None
    return e


# --- happy paths ---
@pytest.mark.asyncio
async def test_yes_side_submits_via_yes_token(executor):
    book = FakeBookStore()
    book.set("YES_TOK", bid=0.54, ask=0.56)
    attempt = await executor.try_buy_continuous(
        signal=_sig(side="YES"), mapping=_mapping(), game=_game(), book_store=book,
    )
    assert attempt.trader_kind == "continuous"
    assert attempt.token_id == "YES_TOK"
    assert attempt.side == "YES"
    assert attempt.order_type == "FAK"
    assert attempt.exit_horizon_sec == 60
    assert attempt.signal_id == "sig-1"
    assert attempt.order_status == "matched"
    # price_cap = ask + 2 ticks = 0.56 + 0.02 = 0.58
    assert attempt.price_cap == pytest.approx(0.58)
    assert executor.client.calls[0]["token_id"] == "YES_TOK"
    assert executor.client.calls[0]["amount_usd"] == 7.5


@pytest.mark.asyncio
async def test_no_side_submits_via_no_token(executor):
    book = FakeBookStore()
    book.set("NO_TOK", bid=0.42, ask=0.44)
    attempt = await executor.try_buy_continuous(
        signal=_sig(side="NO", direction=-1), mapping=_mapping(), game=_game(), book_store=book,
    )
    assert attempt.token_id == "NO_TOK"
    assert attempt.side == "NO"
    assert attempt.order_status == "matched"
    assert executor.client.calls[0]["token_id"] == "NO_TOK"


# --- rejects ---
@pytest.mark.asyncio
async def test_rejects_when_book_missing_ask(executor):
    book = FakeBookStore()
    # Don't set YES_TOK at all
    attempt = await executor.try_buy_continuous(
        signal=_sig(side="YES"), mapping=_mapping(), game=_game(), book_store=book,
    )
    assert attempt.order_status == "rejected_precheck"
    assert attempt.reason_if_rejected == "missing_ask"
    assert attempt.trader_kind == "continuous"
    assert executor.client.calls == []


@pytest.mark.asyncio
async def test_rejects_when_at_max_open_positions(executor, monkeypatch):
    monkeypatch.setattr("live_executor.MAX_OPEN_POSITIONS", 1)
    executor.open_positions = 1
    book = FakeBookStore()
    book.set("YES_TOK", bid=0.54, ask=0.56)
    attempt = await executor.try_buy_continuous(
        signal=_sig(side="YES"), mapping=_mapping(), game=_game(), book_store=book,
    )
    assert attempt.order_status == "rejected_precheck"
    assert attempt.reason_if_rejected == "max_open_positions_reached"


@pytest.mark.asyncio
async def test_rejects_when_at_max_total_usd(executor, monkeypatch):
    monkeypatch.setattr("live_executor.MAX_TOTAL_LIVE_USD", 5.0)
    book = FakeBookStore()
    book.set("YES_TOK", bid=0.54, ask=0.56)
    attempt = await executor.try_buy_continuous(
        signal=_sig(side="YES", sized_usd=10.0), mapping=_mapping(), game=_game(), book_store=book,
    )
    assert attempt.order_status == "rejected_precheck"
    assert attempt.reason_if_rejected == "max_total_live_usd_reached"


@pytest.mark.asyncio
async def test_rejects_when_daily_drawdown_circuit_tripped(executor, monkeypatch):
    monkeypatch.setattr("live_executor.MAX_DAILY_DRAWDOWN_USD", 25.0)
    executor.daily_realized_pnl_usd = -30.0
    book = FakeBookStore()
    book.set("YES_TOK", bid=0.54, ask=0.56)
    attempt = await executor.try_buy_continuous(
        signal=_sig(side="YES"), mapping=_mapping(), game=_game(), book_store=book,
    )
    assert attempt.order_status == "rejected_precheck"
    assert attempt.reason_if_rejected.startswith("daily_drawdown_circuit_breaker")


# --- size capping ---
@pytest.mark.asyncio
async def test_sized_usd_capped_to_max_trade_usd(executor, monkeypatch):
    monkeypatch.setattr("live_executor.MAX_TRADE_USD", 5.0)
    book = FakeBookStore()
    book.set("YES_TOK", bid=0.54, ask=0.56)
    # Signal asks for $15; should be capped to $5.
    attempt = await executor.try_buy_continuous(
        signal=_sig(side="YES", sized_usd=15.0), mapping=_mapping(), game=_game(), book_store=book,
    )
    assert attempt.submitted_size_usd == 5.0
    assert executor.client.calls[0]["amount_usd"] == 5.0


@pytest.mark.asyncio
async def test_does_not_call_client_when_live_disabled(executor, monkeypatch):
    monkeypatch.setattr("live_executor.ENABLE_REAL_LIVE_TRADING", False)
    book = FakeBookStore()
    book.set("YES_TOK", bid=0.54, ask=0.56)
    attempt = await executor.try_buy_continuous(
        signal=_sig(side="YES"), mapping=_mapping(), game=_game(), book_store=book,
    )
    assert attempt.order_status == "filled"
    assert attempt.reason_if_rejected == "paper_simulated"
    assert attempt.filled_size_usd == 7.5
    assert attempt.avg_fill_price == 0.56
    assert executor.client.calls == []
    assert attempt.trader_kind == "continuous"


@pytest.mark.asyncio
async def test_budget_consumed_on_successful_submission(executor):
    book = FakeBookStore()
    book.set("YES_TOK", bid=0.54, ask=0.56)
    assert executor.total_submitted_usd == 0.0
    assert executor.open_positions == 0
    await executor.try_buy_continuous(
        signal=_sig(side="YES"), mapping=_mapping(), game=_game(), book_store=book,
    )
    assert executor.total_submitted_usd == 7.5
    assert executor.open_positions == 1


@pytest.mark.asyncio
async def test_signal_id_propagates_to_attempt(executor):
    book = FakeBookStore()
    book.set("YES_TOK", bid=0.54, ask=0.56)
    sig = _sig()
    attempt = await executor.try_buy_continuous(
        signal=sig, mapping=_mapping(), game=_game(), book_store=book,
    )
    assert attempt.signal_id == sig.signal_id
    assert attempt.exit_horizon_sec == sig.exit_horizon_sec
