from __future__ import annotations

import pytest

from live_position_store import LivePosition, LivePositionStore
from live_reconciliation import reconcile_live_positions


class FakeBalanceClient:
    def __init__(self, balances: dict[str, float]):
        self.balances = balances
        self.calls: list[str] = []

    async def get_conditional_balance(self, token_id: str):
        self.calls.append(token_id)
        return self.balances.get(token_id, 0.0)


class FakeExecutor:
    def __init__(self):
        self.open_positions = 99
        self.saved = False

    def _save(self):
        self.saved = True


def _pos(position_id: str, token_id: str, state: str = "OPEN", shares: float = 10.0):
    return LivePosition(
        position_id=position_id,
        state=state,
        token_id=token_id,
        opposing_token_id=f"opp_{token_id}",
        match_id="M1",
        market_name="Team A vs Team B",
        side="YES",
        entry_price=0.5,
        shares=shares,
        cost_usd=shares * 0.5,
        entry_time_ns=1,
        entry_game_time_sec=None,
        event_type="TEST",
        expected_move=0.0,
        fair_price=0.5,
        pending_entry_order_id="ENTRY1" if state == "PENDING_ENTRY" else None,
        pending_exit_order_id="EXIT1" if state == "PENDING_EXIT_GTC" else None,
    )


def _mappings():
    return [
        {
            "name": "Team A vs Team B",
            "dota_match_id": "M1",
            "yes_token_id": "TOK1",
            "no_token_id": "TOKNO1",
        },
        {
            "name": "Team C vs Team D",
            "dota_match_id": "M2",
            "yes_token_id": "TOK2",
            "no_token_id": "TOKNO2",
        },
    ]


@pytest.mark.asyncio
async def test_reconcile_closes_zero_balance_active_position(tmp_path):
    store = LivePositionStore(str(tmp_path / "positions.json"))
    store.positions["P1"] = _pos("P1", "TOK1", state="PENDING_ENTRY", shares=10.0)
    executor = FakeExecutor()

    result = await reconcile_live_positions(
        client=FakeBalanceClient({"TOK1": 0.0}),
        store=store,
        mappings=_mappings(),
        live_executor=executor,
    )

    assert result.closed_stale == 1
    assert store.positions["P1"].state == "CLOSED"
    assert store.positions["P1"].pending_entry_order_id is None
    assert executor.open_positions == 0
    assert executor.saved is True


@pytest.mark.asyncio
async def test_reconcile_adjusts_existing_positive_balance(tmp_path):
    store = LivePositionStore(str(tmp_path / "positions.json"))
    store.positions["P1"] = _pos("P1", "TOK1", state="PENDING_EXIT_GTC", shares=10.0)

    result = await reconcile_live_positions(
        client=FakeBalanceClient({"TOK1": 7.25}),
        store=store,
        mappings=_mappings(),
    )

    assert result.adjusted_existing == 1
    assert store.positions["P1"].state == "OPEN"
    assert store.positions["P1"].shares == 7.25
    assert store.positions["P1"].pending_exit_order_id is None


@pytest.mark.asyncio
async def test_reconcile_does_not_scan_unrecorded_mapping_tokens(tmp_path):
    store = LivePositionStore(str(tmp_path / "positions.json"))

    result = await reconcile_live_positions(
        client=FakeBalanceClient({"TOK2": 3.5}),
        store=store,
        mappings=_mappings(),
    )

    recovered = store.open_positions()
    assert result.checked_tokens == 0
    assert result.reopened_missing == 0
    assert result.active_after == 0
    assert recovered == []


def test_store_summarize_and_active_count(tmp_path):
    store = LivePositionStore(str(tmp_path / "positions.json"))
    store.positions["P1"] = _pos("P1", "TOK1", state="OPEN")
    store.positions["P2"] = _pos("P2", "TOK2", state="PENDING_ENTRY")
    store.positions["P3"] = _pos("P3", "TOK3", state="PENDING_EXIT_GTC")
    store.positions["P4"] = _pos("P4", "TOK4", state="CLOSED")
    store.positions["P5"] = _pos("P5", "TOK5", state="CLOSED")

    summary = store.summarize()
    assert summary == {"OPEN": 1, "PENDING_ENTRY": 1, "PENDING_EXIT_GTC": 1, "CLOSED": 2}
    # Active = OPEN + PARTIALLY_EXITED + PENDING_ENTRY + PENDING_EXIT_GTC + EXITING
    # = 1 + 0 + 1 + 1 + 0 = 3
    assert store.active_count() == 3
