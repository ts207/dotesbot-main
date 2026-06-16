from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from live_executor import LiveExecutor, LiveExitExecutor, round_down_to_tick
from poly_ws import BookStore


class FakeLiveClient:
    def __init__(self):
        self.calls = []

    async def buy_fak_market(self, **kwargs):
        self.calls.append(kwargs)
        return {"success": True, "status": "matched", "avgFillPrice": kwargs["price_cap"]}


class FakeExitClient:
    def __init__(self):
        self.calls = []

    async def sell_gtc_limit(self, **kwargs):
        self.calls.append(kwargs)
        return {"success": True, "status": "live", "orderID": "exit-order-1"}


def _signal(**overrides):
    base = {
        "event_type": "POLL_BUYBACK_CAPITULATION",
        "cluster_event_types": "POLL_BUYBACK_CAPITULATION",
        "event_direction": "radiant",
        "token_id": "TOKYES",
        "side": "YES",
        "fair_price": 0.72,
        "ask": 0.61,
        "executable_edge": 0.09,
        "lag": 0.09,
        "spread": 0.03,
        "book_age_ms": 100,
        "steam_age_ms": 100,
        "event_schema_version": "cadence_v1",
        "source_cadence_quality": "normal",
        "event_quality": 0.75,
    }
    base.update(overrides)
    return base


def _game():
    return {
        "match_id": "M1",
        "received_at_ns": time.time_ns(),
        "game_over": False,
        "radiant_team": "Team A",
        "dire_team": "Team B",
    }


def _mapping(**overrides):
    base = {
        "name": "Team A vs Team B Game 1",
        "market_type": "MAP_WINNER",
        "yes_team": "Team A",
        "no_team": "Team B",
        "yes_token_id": "TOKYES",
        "no_token_id": "TOKNO",
        "dota_match_id": "M1",
        "confidence": 1.0,
        "tick_size": "0.01",
        "neg_risk": False,
    }
    base.update(overrides)
    return base


def _book_store(ask=0.61, bid=0.58):
    store = BookStore()
    store.update_direct("TOKYES", best_ask=ask, best_bid=bid, ask_size=100, bid_size=100)
    return store


def test_round_down_to_tick():
    assert round_down_to_tick(0.6789, "0.01") == 0.67
    assert round_down_to_tick(0.6789, "0.001") == 0.678


@pytest.mark.asyncio
async def test_forced_exit_does_not_post_invalid_one_dollar_price(monkeypatch):
    monkeypatch.setattr("live_executor.ENABLE_REAL_LIVE_TRADING", True)
    monkeypatch.setattr("live_executor.MAKER_EXIT_MODE", True)
    client = FakeExitClient()
    executor = LiveExitExecutor(client=client)
    position = SimpleNamespace(
        position_id="pos-1",
        token_id="TOKYES",
        match_id="M1",
        shares=10.0,
        entry_price=0.759,
    )

    attempt = await executor.try_exit(
        position=position,
        book={"best_bid": 0.999, "best_ask": 1.0},
        reason="game_over",
        mapping={"tick_size": "0.01", "neg_risk": False},
    )

    assert attempt.order_status == "live"
    assert attempt.price_posted == 0.99
    assert client.calls[0]["price_floor"] == 0.99

@pytest.fixture(autouse=True)
def clean_live_state(monkeypatch):
    monkeypatch.setattr("live_executor.load_live_state", lambda: {"total_submitted_usd": 0.0, "total_filled_usd": 0.0, "open_positions": 0})
    monkeypatch.setattr("live_executor.save_live_state", lambda *a, **kw: None)
    monkeypatch.setattr("live_executor.MAX_TRADE_USD", 1.0)
    monkeypatch.setattr("live_executor.MAX_TOTAL_LIVE_USD", 10.0)
    # Disable edge-weighted sizing by default in tests so existing assertions
    # on submitted_size_usd == MAX_TRADE_USD stay valid. The dedicated B2 test
    # below restores the multiplier to verify the feature.
    monkeypatch.setattr("live_executor.EDGE_SIZE_MAX_MULT", 1.0)
    # Default: balance gate is wide open in tests whose fake client doesn't
    # implement get_usdc_balance. Tests that exercise the gate (test_balance_gate_*)
    # use FakeLiveClientWithBalance, which provides the method, so the real
    # _get_cached_usdc_balance flow runs for them.
    import live_executor as _le
    _orig_get_cached_balance = _le.LiveExecutor._get_cached_usdc_balance
    async def _gated_balance(self):
        if self.client is not None and hasattr(self.client, "get_usdc_balance"):
            return await _orig_get_cached_balance(self)
        return 1_000.0
    monkeypatch.setattr("live_executor.LiveExecutor._get_cached_usdc_balance", _gated_balance)

@pytest.mark.asyncio
async def test_live_executor_sends_capped_fak_buy(monkeypatch):
    monkeypatch.setattr("live_executor.ENABLE_REAL_LIVE_TRADING", True)
    client = FakeLiveClient()
    executor = LiveExecutor(client=client)
    attempt = await executor.try_buy(
        signal=_signal(), mapping=_mapping(), game=_game(), book_store=_book_store()
    )
    assert attempt.order_status == "matched"
    assert attempt.submitted_size_usd == 1
    assert attempt.filled_size_usd == 1
    assert client.calls[0]["amount_usd"] == 1
    # price_cap formula: ask + LIVE_FAK_BUFFER_TICKS*tick. Default buffer is 4
    # ticks so thin/moving books do not miss otherwise valid FAK fills.
    assert client.calls[0]["price_cap"] == 0.65
    assert client.calls[0]["tick_size"] == "0.01"


@pytest.mark.asyncio
async def test_live_executor_allows_tier_b_event_by_default(monkeypatch):
    monkeypatch.setattr("live_executor.ENABLE_REAL_LIVE_TRADING", True)
    client = FakeLiveClient()
    executor = LiveExecutor(client=client)
    attempt = await executor.try_buy(
        signal=_signal(event_type="POLL_FIGHT_SWING", cluster_event_types="POLL_FIGHT_SWING"),
        mapping=_mapping(),
        game=_game(),
        book_store=_book_store(),
    )
    assert attempt.order_status == "matched"
    assert attempt.submitted_size_usd == 1


@pytest.mark.asyncio
async def test_live_executor_rejects_tower_trade_when_disabled(monkeypatch):
    monkeypatch.setattr("live_executor.DISABLE_STRUCTURE_TRADES", True)
    executor = LiveExecutor(client=FakeLiveClient())
    attempt = await executor.try_buy(
        signal=_signal(event_type="BASE_PRESSURE_T3_COLLAPSE", cluster_event_types="BASE_PRESSURE_T3_COLLAPSE"),
        mapping=_mapping(), game=_game(), book_store=_book_store(),
    )
    assert attempt.order_status == "rejected_precheck"
    assert attempt.reason_if_rejected == "structure_trade_disabled"


@pytest.mark.asyncio
async def test_live_executor_rejects_non_cadence_or_stale_quality_events(monkeypatch):
    monkeypatch.setattr("live_executor.TRADE_EVENTS", {"POLL_BUYBACK_CAPITULATION"})
    executor = LiveExecutor(client=FakeLiveClient())
    missing_schema = await executor.try_buy(
        signal=_signal(event_schema_version=None),
        mapping=_mapping(),
        game=_game(),
        book_store=_book_store(),
    )
    assert missing_schema.order_status == "rejected_precheck"
    assert missing_schema.reason_if_rejected == "missing_cadence_event_schema"

    stale = await executor.try_buy(
        signal=_signal(source_cadence_quality="stale_gap"),
        mapping=_mapping(),
        game=_game(),
        book_store=_book_store(),
    )
    assert stale.order_status == "rejected_precheck"
    assert stale.reason_if_rejected.startswith("cadence_quality_not_live_allowed")


@pytest.mark.asyncio
async def test_live_executor_rejects_low_quality_event(monkeypatch):
    monkeypatch.setattr("live_executor.TRADE_EVENTS", {"POLL_BUYBACK_CAPITULATION"})
    # Pin the threshold so env-set values (e.g. LIVE_MIN_EVENT_QUALITY=0.20)
    # don't make a 0.2-quality signal pass.
    monkeypatch.setattr("live_executor.LIVE_MIN_EVENT_QUALITY", 0.5)
    executor = LiveExecutor(client=FakeLiveClient())
    attempt = await executor.try_buy(
        signal=_signal(event_quality=0.2),
        mapping=_mapping(),
        game=_game(),
        book_store=_book_store(),
    )
    assert attempt.order_status == "rejected_precheck"
    assert attempt.reason_if_rejected.startswith("event_quality_too_low")


@pytest.mark.asyncio
async def test_live_executor_rejects_if_ask_above_event_max_fill(monkeypatch):
    executor = LiveExecutor(client=FakeLiveClient())
    attempt = await executor.try_buy(
        signal=_signal(fair_price=0.72, executable_edge=0.09, lag=0.09, max_fill_price=0.70),
        mapping=_mapping(), game=_game(), book_store=_book_store(ask=0.71, bid=0.69),
    )
    assert attempt.order_status == "rejected_precheck"
    assert attempt.reason_if_rejected.startswith("ask_above_event_max_fill")


@pytest.mark.asyncio
async def test_live_executor_budget_caps_after_ten_attempts(monkeypatch):
    monkeypatch.setattr("live_executor.ENABLE_REAL_LIVE_TRADING", True)
    monkeypatch.setattr("live_executor.MAX_OPEN_POSITIONS", 999)
    client = FakeLiveClient()
    executor = LiveExecutor(client=client)
    for _ in range(10):
        attempt = await executor.try_buy(
            signal=_signal(), mapping=_mapping(), game=_game(), book_store=_book_store()
        )
        assert attempt.submitted_size_usd == 1
        # For this budget-specific test, treat fills as closed so the open-position
        # guard does not stop before the max-total-spend guard. Also reset the
        # per-match dedup map so the next iteration isn't blocked by
        # match_already_submitted from the prior loop.
        executor.open_positions = 0
        executor._submitted_match_sides.clear()
    attempt = await executor.try_buy(
        signal=_signal(), mapping=_mapping(), game=_game(), book_store=_book_store()
    )
    assert attempt.reason_if_rejected == "max_total_live_usd_reached"


@pytest.mark.asyncio
async def test_live_executor_uses_event_specific_max_fill_above_default(monkeypatch):
    monkeypatch.setattr("live_executor.ENABLE_REAL_LIVE_TRADING", True)
    client = FakeLiveClient()
    executor = LiveExecutor(client=client)
    sig = _signal(
        event_type="OBJECTIVE_CONVERSION_T3",
        cluster_event_types="OBJECTIVE_CONVERSION_T3",
        fair_price=0.88,
        executable_edge=0.08,
        lag=0.08,
        max_fill_price=0.88,
    )
    attempt = await executor.try_buy(
        signal=sig, mapping=_mapping(), game=_game(), book_store=_book_store(ask=0.82, bid=0.80)
    )
    assert attempt.order_status == "matched"
    # price_cap formula: min(ask + 4*tick, event_max_fill) — for ask=0.82 with
    # tick=0.01 that's 0.86; OBJECTIVE_CONVERSION_T3's higher event_max_fill
    # doesn't override the ask-based ceiling.
    assert client.calls[0]["price_cap"] == 0.86


@pytest.mark.asyncio
async def test_live_executor_rejects_above_event_max_fill():
    executor = LiveExecutor(client=FakeLiveClient())
    sig = _signal(
        event_type="OBJECTIVE_CONVERSION_T3",
        cluster_event_types="OBJECTIVE_CONVERSION_T3",
        fair_price=0.95,
        executable_edge=0.10,
        lag=0.10,
        max_fill_price=0.88,
    )
    attempt = await executor.try_buy(
        signal=sig, mapping=_mapping(), game=_game(), book_store=_book_store(ask=0.89, bid=0.87)
    )
    assert attempt.order_status == "rejected_precheck"
    assert attempt.reason_if_rejected.startswith("ask_above_event_max_fill")


@pytest.mark.asyncio
async def test_live_executor_rejects_mapping_confidence_below_one():
    executor = LiveExecutor(client=FakeLiveClient())
    attempt = await executor.try_buy(
        signal=_signal(), mapping=_mapping(confidence=0.99), game=_game(), book_store=_book_store()
    )
    assert attempt.order_status == "rejected_precheck"
    assert attempt.reason_if_rejected.startswith("mapping_invalid:")


@pytest.mark.asyncio
async def test_live_executor_operator_allowlist_overrides_tier_c(monkeypatch):
    monkeypatch.setattr("live_executor.TRADE_EVENTS", {"OBJECTIVE_CONVERSION_T2"})
    monkeypatch.setattr("live_executor.ALLOW_CONFIRMATION_ONLY_LIVE_TRADES", False)
    monkeypatch.setattr("live_executor.DISABLE_STRUCTURE_TRADES", False)
    monkeypatch.setattr("live_executor.ENABLE_REAL_LIVE_TRADING", True)
    # Monkeypatch event_tier or TIER_C_EVENTS to make OBJECTIVE_CONVERSION_T2 a Tier C event for this test
    monkeypatch.setattr("live_executor.event_tier", lambda e: "C" if e == "OBJECTIVE_CONVERSION_T2" else "unknown")
    
    executor = LiveExecutor(client=FakeLiveClient())
    attempt = await executor.try_buy(
        signal=_signal(event_type="OBJECTIVE_CONVERSION_T2", cluster_event_types="OBJECTIVE_CONVERSION_T2"),
        mapping=_mapping(),
        game=_game(),
        book_store=_book_store(),
    )
    assert attempt.order_status == "matched"


@pytest.mark.asyncio
async def test_live_executor_rejects_priced_objective_conversion_t3_without_large_edge(monkeypatch):
    monkeypatch.setattr("live_executor.TRADE_EVENTS", {"OBJECTIVE_CONVERSION_T3"})
    executor = LiveExecutor(client=FakeLiveClient())
    sig = _signal(
        event_type="OBJECTIVE_CONVERSION_T3",
        cluster_event_types="OBJECTIVE_CONVERSION_T3",
        ask=0.87,
        fair_price=0.93,
        executable_edge=0.06,
        lag=0.08,
        max_fill_price=0.90,
    )
    attempt = await executor.try_buy(
        signal=sig, mapping=_mapping(), game=_game(), book_store=_book_store(ask=0.87, bid=0.85)
    )
    assert attempt.order_status == "rejected_precheck"
    assert attempt.reason_if_rejected.startswith("objective_conversion_t3_requires_8c_edge_above_85c")


@pytest.mark.asyncio
async def test_live_executor_rejects_terminal_price_chasing_before_submit(monkeypatch):
    monkeypatch.setattr("live_executor.TRADE_EVENTS", {"OBJECTIVE_CONVERSION_T4"})
    executor = LiveExecutor(client=FakeLiveClient())
    sig = _signal(
        event_type="OBJECTIVE_CONVERSION_T4",
        cluster_event_types="OBJECTIVE_CONVERSION_T4",
        ask=0.96,
        fair_price=0.99,
        executable_edge=0.10,
        lag=0.08,
        max_fill_price=0.98,
    )
    attempt = await executor.try_buy(
        signal=sig, mapping=_mapping(), game=_game(), book_store=_book_store(ask=0.96, bid=0.94)
    )
    assert attempt.order_status == "rejected_precheck"
    assert attempt.reason_if_rejected.startswith("chasing_terminal_price")


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", [
    "LOW_PRICE_UNDERDOG_COUNTERPUNCH",
    "LATE_CHEAP_LEAD_SWING_REPRICE",
])
async def test_live_executor_operator_allowlist_overrides_research_taxonomy(monkeypatch, event_type):
    monkeypatch.setattr("live_executor.TRADE_EVENTS", {event_type})
    monkeypatch.setattr("live_executor.ENABLE_REAL_LIVE_TRADING", True)
    executor = LiveExecutor(client=FakeLiveClient())
    attempt = await executor.try_buy(
        signal=_signal(event_type=event_type, cluster_event_types=event_type),
        mapping=_mapping(),
        game=_game(),
        book_store=_book_store(),
    )
    assert attempt.order_status == "matched"


@pytest.mark.asyncio
async def test_edge_weighted_sizing_scales_with_fresh_edge(monkeypatch):
    """B2: order_usd should grow with edge, capped at EDGE_SIZE_MAX_MULT."""
    monkeypatch.setattr("live_executor.ENABLE_REAL_LIVE_TRADING", True)
    monkeypatch.setattr("live_executor.EDGE_SIZE_MAX_MULT", 2.0)
    monkeypatch.setattr("live_executor.MAX_TRADE_USD", 5.0)
    monkeypatch.setattr("live_executor.MAX_TOTAL_LIVE_USD", 100.0)
    client = FakeLiveClient()
    executor = LiveExecutor(client=client)
    # _signal fresh edge ≈ fair(0.72) - ask(0.61) = 0.11; mult = min(2.0, 0.11/0.05) = 2.0.
    attempt = await executor.try_buy(
        signal=_signal(), mapping=_mapping(), game=_game(), book_store=_book_store()
    )
    assert attempt.order_status == "matched"
    assert attempt.submitted_size_usd == 10.0  # MAX_TRADE_USD (5) * mult (2.0)


@pytest.mark.asyncio
async def test_per_match_exposure_cap_blocks_after_threshold(monkeypatch):
    """B1: cumulative USD on a single match capped at MAX_OPEN_USD_PER_MATCH."""
    monkeypatch.setattr("live_executor.ENABLE_REAL_LIVE_TRADING", True)
    monkeypatch.setattr("live_executor.MAX_OPEN_USD_PER_MATCH", 3.0)
    monkeypatch.setattr("live_executor.MAX_OPEN_POSITIONS", 999)
    client = FakeLiveClient()
    executor = LiveExecutor(client=client)
    # First trade fills, takes match exposure to 1.0 (MAX_TRADE_USD).
    await executor.try_buy(
        signal=_signal(), mapping=_mapping(), game=_game(), book_store=_book_store()
    )
    # Second trade on a different signal/direction would also count to the match.
    # But same direction is blocked by match_already_submitted first; clear it.
    executor._submitted_match_sides.clear()
    await executor.try_buy(
        signal=_signal(), mapping=_mapping(), game=_game(), book_store=_book_store()
    )
    # Third — match exposure now 2.0; cap is 3.0; third would be 3.0 total → ok.
    executor._submitted_match_sides.clear()
    third = await executor.try_buy(
        signal=_signal(), mapping=_mapping(), game=_game(), book_store=_book_store()
    )
    assert third.order_status == "matched"
    # Fourth — match exposure now 3.0; cap reached. Should reject.
    executor._submitted_match_sides.clear()
    fourth = await executor.try_buy(
        signal=_signal(), mapping=_mapping(), game=_game(), book_store=_book_store()
    )
    assert fourth.order_status == "rejected_precheck"
    assert fourth.reason_if_rejected.startswith("max_open_usd_per_match_reached")


@pytest.mark.asyncio
async def test_reject_reasons_carry_numeric_values_for_funnel(monkeypatch):
    """B3 funnel relies on reject reasons carrying their triggering values
    after a ':' separator. Lock in the contract for the high-leverage reasons."""
    monkeypatch.setattr("live_executor.TRADE_EVENTS", {"POLL_BUYBACK_CAPITULATION"})
    monkeypatch.setattr("live_executor.LIVE_MIN_EVENT_QUALITY", 0.5)
    executor = LiveExecutor(client=FakeLiveClient())

    low_quality = await executor.try_buy(
        signal=_signal(event_quality=0.2), mapping=_mapping(),
        game=_game(), book_store=_book_store(),
    )
    assert low_quality.reason_if_rejected.startswith("event_quality_too_low:")
    assert "q=0.200" in low_quality.reason_if_rejected
    assert "min=0.500" in low_quality.reason_if_rejected

    monkeypatch.setattr("live_executor.TRADE_EVENTS", {"POLL_TEAM_WIPE"})
    monkeypatch.setattr("live_executor.MIN_EXECUTABLE_EDGE", 0.99)
    no_edge = await executor.try_buy(
        signal=_signal(
            event_type="POLL_TEAM_WIPE",
            cluster_event_types="POLL_TEAM_WIPE",
            executable_edge=0.05,
        ),
        mapping=_mapping(),
        game=_game(), book_store=_book_store(),
    )
    assert no_edge.reason_if_rejected.startswith("edge_too_small:")
    assert "edge=0.0500" in no_edge.reason_if_rejected
    assert "min=0.9900" in no_edge.reason_if_rejected


class FakeLiveClientWithBalance(FakeLiveClient):
    def __init__(self, balance_usd: float | None = None, raise_on_balance: bool = False):
        super().__init__()
        self._balance_usd = balance_usd
        self._raise_on_balance = raise_on_balance
        self.balance_calls = 0

    async def get_usdc_balance(self):
        self.balance_calls += 1
        if self._raise_on_balance:
            raise RuntimeError("simulated balance fetch failure")
        return self._balance_usd


@pytest.mark.asyncio
async def test_balance_gate_allows_when_balance_sufficient(monkeypatch):
    monkeypatch.setattr("live_executor.ENABLE_REAL_LIVE_TRADING", True)
    client = FakeLiveClientWithBalance(balance_usd=5.0)
    executor = LiveExecutor(client=client)
    attempt = await executor.try_buy(
        signal=_signal(), mapping=_mapping(), game=_game(), book_store=_book_store()
    )
    assert attempt.order_status == "matched"
    assert client.balance_calls == 1


@pytest.mark.asyncio
async def test_disk_guard_rejects_before_submit(monkeypatch):
    monkeypatch.setattr("live_executor.ENABLE_REAL_LIVE_TRADING", True)
    client = FakeLiveClientWithBalance(balance_usd=5.0)
    executor = LiveExecutor(client=client)
    monkeypatch.setattr(
        executor.disk_guard,
        "reject_reason",
        lambda: "disk_guard_low_free_space:free_gb=1.00_min_gb=2.00",
    )
    attempt = await executor.try_buy(
        signal=_signal(), mapping=_mapping(), game=_game(), book_store=_book_store()
    )
    assert attempt.order_status == "rejected_precheck"
    assert attempt.reason_if_rejected.startswith("disk_guard_low_free_space")
    assert client.calls == []


@pytest.mark.asyncio
async def test_balance_gate_rejects_when_balance_below_order_usd(monkeypatch):
    monkeypatch.setattr("live_executor.ENABLE_REAL_LIVE_TRADING", True)
    client = FakeLiveClientWithBalance(balance_usd=0.50)
    executor = LiveExecutor(client=client)
    attempt = await executor.try_buy(
        signal=_signal(), mapping=_mapping(), game=_game(), book_store=_book_store()
    )
    assert attempt.order_status == "rejected_precheck"
    assert attempt.reason_if_rejected.startswith("insufficient_balance_cached")
    assert "bal=0.5000" in attempt.reason_if_rejected
    assert "need=1.0000" in attempt.reason_if_rejected
    assert client.calls == []  # never reached submission


@pytest.mark.asyncio
async def test_balance_gate_fails_open_when_fetch_raises(monkeypatch):
    """No usable cache + fetch failure → gate passes (don't block trading on API hiccup)."""
    monkeypatch.setattr("live_executor.ENABLE_REAL_LIVE_TRADING", True)
    client = FakeLiveClientWithBalance(raise_on_balance=True)
    executor = LiveExecutor(client=client)
    attempt = await executor.try_buy(
        signal=_signal(), mapping=_mapping(), game=_game(), book_store=_book_store()
    )
    assert attempt.order_status == "matched"
    assert client.balance_calls == 1
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_balance_gate_caches_balance_across_attempts(monkeypatch):
    """Within the TTL window, balance should be fetched only once."""
    monkeypatch.setattr("live_executor.ENABLE_REAL_LIVE_TRADING", True)
    client = FakeLiveClientWithBalance(balance_usd=10.0)
    executor = LiveExecutor(client=client)
    for _ in range(3):
        await executor.try_buy(
            signal=_signal(), mapping=_mapping(), game=_game(), book_store=_book_store()
        )
    assert client.balance_calls == 1  # cached after first


@pytest.mark.asyncio
async def test_balance_gate_persists_snapshot_on_successful_fetch(monkeypatch, tmp_path):
    """Successful balance fetch should mirror to logs/usdc_balance.json so the
    out-of-process dashboard can read it."""
    monkeypatch.setattr("live_executor.ENABLE_REAL_LIVE_TRADING", True)
    snapshot_path = tmp_path / "usdc_balance.json"
    monkeypatch.setattr("live_executor._USDC_BALANCE_PATH", str(snapshot_path))
    client = FakeLiveClientWithBalance(balance_usd=42.5)
    executor = LiveExecutor(client=client)
    await executor.try_buy(
        signal=_signal(), mapping=_mapping(), game=_game(), book_store=_book_store()
    )
    import json as _json
    payload = _json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert payload["usdc_balance"] == 42.5
    assert payload["checked_at_ns"] > 0


@pytest.mark.asyncio
async def test_balance_gate_uses_stale_cache_on_fetch_failure(monkeypatch):
    """Once we have a cached balance, transient fetch failures fall back to it."""
    monkeypatch.setattr("live_executor.ENABLE_REAL_LIVE_TRADING", True)
    monkeypatch.setattr(LiveExecutor, "BALANCE_CACHE_TTL_SEC", 0.0)  # force refresh every call
    client = FakeLiveClientWithBalance(balance_usd=10.0)
    executor = LiveExecutor(client=client)
    await executor.try_buy(
        signal=_signal(), mapping=_mapping(),
        game={"match_id": "M_first", "received_at_ns": time.time_ns(), "game_over": False,
              "radiant_team": "A", "dire_team": "B"},
        book_store=_book_store(),
    )
    # Flip to raising; cached value should keep the gate open on a new match.
    client._raise_on_balance = True
    attempt = await executor.try_buy(
        signal=_signal(), mapping=_mapping(),
        game={"match_id": "M_second", "received_at_ns": time.time_ns(), "game_over": False,
              "radiant_team": "A", "dire_team": "B"},
        book_store=_book_store(),
    )
    assert attempt.order_status == "matched"


@pytest.mark.asyncio
async def test_delayed_poll_credits_fill_only_after_confirmation(monkeypatch):
    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr("live_executor.asyncio.sleep", _no_sleep)
    executor = LiveExecutor(client=FakeLiveClient())
    executor.total_submitted_usd = 1.0
    executor.total_filled_usd = 0.0
    executor.open_positions = 1
    attempt = _signal()
    live_attempt = executor._reject(attempt, _mapping(), _game(), "placeholder")
    live_attempt.order_status = "delayed"
    live_attempt.submitted_size_usd = 1.0
    emitted = []
    executor.set_delayed_resolution_callback(lambda resolved: emitted.append(resolved.to_dict()))

    async def _status(_order_id):
        return {"status": "filled", "filled_size_usd": 0.75}

    monkeypatch.setattr(executor, "_poll_order_status", _status)
    await executor._poll_and_cancel_delayed(
        order_id="O1", order_usd=1.0, match_id="M1", attempt=live_attempt
    )

    assert executor.total_filled_usd == 0.75
    assert executor.total_submitted_usd == 1.0
    assert executor.open_positions == 1
    assert live_attempt.order_status == "filled"
    assert live_attempt.filled_size_usd == 0.75
    assert emitted[-1]["order_status"] == "filled"


@pytest.mark.asyncio
async def test_delayed_poll_releases_budget_on_cancelled_status(monkeypatch):
    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr("live_executor.asyncio.sleep", _no_sleep)
    executor = LiveExecutor(client=FakeLiveClient())
    executor.total_submitted_usd = 1.0
    executor.total_filled_usd = 0.0
    executor.open_positions = 1
    executor._submitted_match_sides["M1"] = "radiant"
    live_attempt = executor._reject(_signal(), _mapping(), _game(), "placeholder")
    live_attempt.order_status = "delayed"
    live_attempt.submitted_size_usd = 1.0
    emitted = []
    executor.set_delayed_resolution_callback(lambda resolved: emitted.append(resolved.to_dict()))

    async def _status(_order_id):
        return {"status": "canceled"}

    monkeypatch.setattr(executor, "_poll_order_status", _status)
    await executor._poll_and_cancel_delayed(
        order_id="O1", order_usd=1.0, match_id="M1", attempt=live_attempt
    )

    assert executor.total_filled_usd == 0.0
    assert executor.total_submitted_usd == 0.0
    assert executor.open_positions == 0
    assert "M1" not in executor._submitted_match_sides
    assert live_attempt.order_status == "canceled"
    assert live_attempt.reason_if_rejected == "delayed_order_canceled"
    assert emitted[-1]["order_status"] == "canceled"
