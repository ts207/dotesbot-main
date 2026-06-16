"""Smoke tests for the scalp executor — filter, P&L tracking, retry logic."""
from __future__ import annotations

import os
import pytest

# Force-enable scalp for tests BEFORE importing the module
os.environ["SCALP_ENABLED"] = "true"
os.environ["SCALP_STAKE_USD"] = "10"

import scalp_executor as se
se.SCALP_ENABLED = True
se.ENABLE_REAL_LIVE_TRADING = True


class FakeClient:
    """In-memory CLOB stub.

    By default, GTC buys are "resting" (returns order_id, zero fill).
    Subsequent check_gtc_fill returns the response we configure per order_id.
    """

    def __init__(self):
        self.buy_responses: list[dict] = []
        self.sell_responses: list[dict] = []
        self.fill_responses: dict[str, dict] = {}
        self.next_order_id = 1

    async def buy_gtc_limit(self, **kwargs):
        oid = f"BUY{self.next_order_id}"
        self.next_order_id += 1
        if self.buy_responses:
            resp = self.buy_responses.pop(0)
            resp.setdefault("orderID", oid)
            return resp
        return {"orderID": oid, "status": "live", "filledShares": 0}

    async def sell_gtc_limit(self, **kwargs):
        oid = f"SELL{self.next_order_id}"
        self.next_order_id += 1
        if self.sell_responses:
            resp = self.sell_responses.pop(0)
            resp.setdefault("orderID", oid)
            return resp
        return {"orderID": oid, "status": "live", "filledShares": 0}

    async def check_gtc_fill(self, order_id):
        return self.fill_responses.get(order_id, {"status": "live"})


def test_qualifies_filter():
    ok, _ = se.ScalpExecutor.qualifies(0.48, 0.55)
    assert ok
    ok, why = se.ScalpExecutor.qualifies(0.60, 0.45)
    assert not ok and "skew" in why
    ok, why = se.ScalpExecutor.qualifies(0.55, 0.55)
    assert not ok and "sum" in why
    ok, why = se.ScalpExecutor.qualifies(0.30, 0.30)
    assert not ok and "price_out_of_range" in why
    ok, why = se.ScalpExecutor.qualifies(0.50, 0.50, game_started=True)
    assert not ok and "post_kickoff" in why


@pytest.mark.asyncio
async def test_evaluate_opens_pair_when_filter_passes():
    client = FakeClient()
    ex = se.ScalpExecutor(clob_client=client)
    result = await ex.evaluate_market(
        market_id="M1", match_id="123",
        yes_token="YES1", no_token="NO1",
        yes_ask=0.48, no_ask=0.52,
        tick_size="0.01", neg_risk=False, game_started=False,
    )
    assert result["action"] == "scalp_opened_pair"
    assert ex.open_pairs() == 1
    pair = ex._pairs["M1"]
    assert pair.yes.buy_order_id == "BUY1"
    assert pair.no.buy_order_id == "BUY2"
    assert pair.yes.intended_shares == pytest.approx(se.SCALP_STAKE_USD / 0.48, abs=0.001)


@pytest.mark.asyncio
async def test_skips_when_filter_fails():
    client = FakeClient()
    ex = se.ScalpExecutor(clob_client=client)
    result = await ex.evaluate_market(
        market_id="M1", match_id="123",
        yes_token="YES1", no_token="NO1",
        yes_ask=0.60, no_ask=0.45,  # in price range, skew 0.15 > 0.08
        tick_size="0.01", neg_risk=False, game_started=False,
    )
    assert result["action"] == "skip"
    assert "skew" in result["reason"]
    assert ex.open_pairs() == 0


@pytest.mark.asyncio
async def test_pnl_tracked_on_scratch_fill():
    client = FakeClient()
    ex = se.ScalpExecutor(clob_client=client)
    await ex.evaluate_market(
        market_id="M1", match_id="123",
        yes_token="YES1", no_token="NO1",
        yes_ask=0.48, no_ask=0.52,
        tick_size="0.01", neg_risk=False, game_started=False,
    )
    pair = ex._pairs["M1"]

    # Mark both buys as filled at the intended price (skipping the poll roundtrip)
    pair.yes.buy_filled = True
    pair.yes.filled_shares = 20.0
    pair.yes.filled_avg_px = 0.48
    pair.no.buy_filled = True
    pair.no.filled_shares = 18.0
    pair.no.filled_avg_px = 0.52

    # Tick once to trigger scratch placement
    await ex.on_book_tick(
        market_id="M1", yes_ask=0.49, yes_bid=0.48,
        no_ask=0.53, no_bid=0.52, game_over=False,
        tick_size="0.01", neg_risk=False,
    )
    assert pair.yes.scratch_order_id is not None
    assert pair.no.scratch_order_id is not None

    # Simulate YES scratch fills @ 0.50, then ride NO to 0.95
    client.fill_responses[pair.yes.scratch_order_id] = {
        "status": "matched", "filledShares": 20.0, "avgFillPrice": 0.50,
    }
    await ex.poll_fills(client.check_gtc_fill)
    assert pair.yes.scratch_filled
    # YES leg P&L: (0.50 - 0.48) * 20 - (0.48 + 0.50) * 20 * 0.02
    expected_yes_pnl = (0.50 - 0.48) * 20 - (0.48 + 0.50) * 20 * 0.02
    assert pair.yes.realized_pnl == pytest.approx(expected_yes_pnl)
    assert pair.ride_token is None  # not yet — on_book_tick decides

    # Tick with NO bid at 0.95 → ride takes profit
    await ex.on_book_tick(
        market_id="M1", yes_ask=0.51, yes_bid=0.50,
        no_ask=0.96, no_bid=0.95, game_over=False,
        tick_size="0.01", neg_risk=False,
    )
    assert pair.ride_token == "NO1"
    assert pair.closed
    expected_no_pnl = (0.95 - 0.52) * 18 - (0.52 + 0.95) * 18 * 0.02
    assert pair.no.realized_pnl == pytest.approx(expected_no_pnl, rel=0.01)
    assert pair.realized_pnl_usd == pytest.approx(expected_yes_pnl + expected_no_pnl, rel=0.01)


@pytest.mark.asyncio
async def test_buy_retry_on_rejection():
    client = FakeClient()
    # First buy attempt: order rejected (no order_id returned)
    client.buy_responses = [
        {"orderID": None, "status": "rejected"},
        {"orderID": None, "status": "rejected"},  # YES rejection
        {"orderID": "BUY-RETRY-1", "status": "live", "filledShares": 0},  # YES retry
    ]
    ex = se.ScalpExecutor(clob_client=client)
    await ex.evaluate_market(
        market_id="M1", match_id="123",
        yes_token="YES1", no_token="NO1",
        yes_ask=0.48, no_ask=0.52,
        tick_size="0.01", neg_risk=False, game_started=False,
    )
    pair = ex._pairs["M1"]
    assert pair.yes.buy_order_id is None
    assert pair.yes.buy_attempts == 1

    # Tick → retry kicks in
    await ex.on_book_tick(
        market_id="M1", yes_ask=0.48, yes_bid=0.47,
        no_ask=0.53, no_bid=0.52, game_over=False,
        tick_size="0.01", neg_risk=False,
    )
    assert pair.yes.buy_attempts == 2
    assert pair.yes.buy_order_id == "BUY-RETRY-1"
