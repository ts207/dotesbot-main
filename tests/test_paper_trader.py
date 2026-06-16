import time
import csv

import pytest

from paper_trader import PaperTrader, Position
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


def _signal(**overrides):
    data = {
        "ask": 0.50,
        "bid": 0.48,
        "fair_price": 0.70,
        "target_size_usd": 25,
        "game_time_sec": 1200,
        "event_type": "BASE_PRESSURE_T4",
        "lag": 0.15,
        "expected_move": 0.22,
    }
    data.update(overrides)
    return data


def test_paper_entry_fills_at_ask_not_mid():
    trader = PaperTrader()
    store = Store({"YES": {"best_ask": 0.50, "best_bid": 0.40, "ask_size": 100}})

    pos, reason = trader.enter(
        signal=_signal(), token_id="YES", side="YES", book_store=store,
        match_id="M1", market_name="Test", opposing_token_id="NO",
    )

    assert reason == "filled"
    assert pos is not None
    assert pos.entry_price == pytest.approx(0.50)
    assert pos.shares == pytest.approx(50.0)


def test_paper_entry_persists_strategy_metadata():
    trader = PaperTrader()
    store = Store({"YES": {"best_ask": 0.50, "best_bid": 0.40, "ask_size": 100}})

    pos, reason = trader.enter(
        signal=_signal(
            event_type="EVENT_TRIGGERED_VALUE",
            event_family="VALUE",
            hold_policy="thesis_invalidation",
            actual_event_type="NETWORTH_SWING_WINDOW",
            executable_edge=0.12,
            event_direction="radiant",
            lead=7000,
            derived_state_flags="DOMINANT_NETWORTH_LEAD,PUSH_SETUP_STATE",
        ),
        token_id="YES",
        side="YES",
        book_store=store,
        match_id="M1",
        market_name="Test",
        opposing_token_id="NO",
    )

    assert reason == "filled"
    assert pos is not None
    assert pos.strategy_kind == "VALUE"
    assert pos.hold_policy == "thesis_invalidation"
    assert pos.entry_fair == pytest.approx(0.70)
    assert pos.entry_edge == pytest.approx(0.12)
    assert pos.entry_backed_side == "radiant"
    assert pos.entry_radiant_lead == 7000
    assert pos.entry_actual_event_type == "NETWORTH_SWING_WINDOW"
    assert pos.entry_derived_state_flags == ["DOMINANT_NETWORTH_LEAD", "PUSH_SETUP_STATE"]

    closed = trader.force_exit("YES", Store({"YES": {"best_bid": 0.60, "best_ask": 0.62}}), "test")
    assert closed is not None
    assert closed.strategy_kind == "VALUE"
    assert closed.entry_derived_state_flags == ["DOMINANT_NETWORTH_LEAD", "PUSH_SETUP_STATE"]


def test_paper_entry_rejects_when_ask_moves_above_limit():
    trader = PaperTrader()
    store = Store({"YES": {"best_ask": 0.53, "best_bid": 0.40, "ask_size": 100}})

    pos, reason = trader.enter(
        signal=_signal(ask=0.50, fair_price=0.70), token_id="YES", side="YES", book_store=store,
        match_id="M1", market_name="Test", opposing_token_id="NO",
    )

    assert pos is None
    assert reason.startswith("ask_moved_above_limit")


def test_force_exit_sells_at_bid_not_mid():
    trader = PaperTrader()
    trader.positions["YES"] = Position(
        token_id="YES", match_id="M1", market_name="Test", side="YES",
        entry_price=0.50, shares=50, cost_usd=25, entry_time_ns=time.time_ns(),
        entry_game_time_sec=1200, event_type="BASE_PRESSURE_T4", lag=0.1, expected_move=0.2,
    )
    trader._match_open_usd["M1"] = 25
    store = Store({"YES": {"best_bid": 0.60, "best_ask": 0.80}})

    closed = trader.force_exit("YES", store, "test")

    assert closed is not None
    assert closed.exit_price == pytest.approx(0.60)
    assert closed.pnl_usd == pytest.approx(5.0)


def test_reentry_cooldown_blocks_immediate_rebuy_after_exit():
    trader = PaperTrader()
    store = Store({"YES": {"best_ask": 0.50, "best_bid": 0.48, "ask_size": 100}})

    pos, reason = trader.enter(
        signal=_signal(event_type="ML_ARBITRAGE", fair_price=0.80, expected_move=0.20),
        token_id="YES", side="YES", book_store=store,
        match_id="M1", market_name="Test", opposing_token_id="NO",
    )
    assert pos is not None, reason

    closed = trader.force_exit("YES", store, "stop_loss")
    assert closed is not None

    rebuy, reason = trader.enter(
        signal=_signal(event_type="ML_ARBITRAGE", fair_price=0.80, expected_move=0.20),
        token_id="YES", side="YES", book_store=store,
        match_id="M1", market_name="Test", opposing_token_id="NO",
    )
    assert rebuy is None
    assert reason.startswith("reentry_cooldown")


def test_take_profit_uses_fair_price_not_entry_plus_expected_move():
    trader = PaperTrader()
    pos, reason = trader.enter(
        signal=_signal(ask=0.56, fair_price=0.67, expected_move=0.22),
        token_id="YES", side="YES",
        book_store=Store({"YES": {"best_ask": 0.56, "best_bid": 0.54, "ask_size": 100}}),
        match_id="M1", market_name="Test", opposing_token_id="NO",
    )
    assert reason == "filled"
    # Bid reaches model fair price (0.67) but not entry+expected_move (0.78).
    closed = trader.check_exits(Store({"YES": {"best_bid": 0.67, "best_ask": 0.69}}), set())
    assert len(closed) == 1
    assert closed[0].exit_reason == "take_profit"


def test_dynamic_model_fair_can_trigger_value_exit():
    trader = PaperTrader()
    pos, reason = trader.enter(
        signal=_signal(ask=0.50, fair_price=0.70, expected_move=0.20),
        token_id="YES", side="YES",
        book_store=Store({"YES": {"best_ask": 0.50, "best_bid": 0.48, "ask_size": 100}}),
        match_id="M1", market_name="Test", opposing_token_id="NO",
    )
    assert pos is not None, reason

    trader.update_fair_value("YES", 0.49)
    closed = trader.check_exits(Store({"YES": {"best_bid": 0.49, "best_ask": 0.51}}), set())

    assert len(closed) == 1
    assert closed[0].exit_reason == "model_value_exit"


def test_value_exit_policy_uses_current_game_for_catastrophe_salvage():
    trader = PaperTrader()
    trader.positions["YES"] = Position(
        token_id="YES",
        match_id="M1",
        market_name="Test",
        side="YES",
        entry_price=0.50,
        shares=50,
        cost_usd=25,
        entry_time_ns=time.time_ns() - 60_000_000_000,
        entry_game_time_sec=1200,
        event_type="VALUE",
        lag=0.0,
        expected_move=0.0,
        fair_price=0.80,
        strategy_kind="VALUE_EDGE",
        hold_policy="thesis_invalidation",
        entry_backed_side="radiant",
    )
    trader._match_open_usd["M1"] = 25

    closed = trader.check_exits(
        Store({"YES": {"best_bid": 0.10, "best_ask": 0.12}}),
        set(),
        current_games_by_match_id={"M1": {"radiant_lead": -3000}},
    )

    assert len(closed) == 1
    assert closed[0].exit_reason == "catastrophe_salvage"


def test_latency_edge_timeout_exits_after_average_edge_window(monkeypatch):
    monkeypatch.setattr("paper_trader.EXIT_LATENCY_EDGE_SEC", 1)
    trader = PaperTrader()
    pos, reason = trader.enter(
        signal=_signal(ask=0.50, fair_price=0.70, expected_move=0.20),
        token_id="YES", side="YES",
        book_store=Store({"YES": {"best_ask": 0.50, "best_bid": 0.48, "ask_size": 100}}),
        match_id="M1", market_name="Test", opposing_token_id="NO",
    )
    assert pos is not None, reason
    pos.entry_time_ns = time.time_ns() - 2_000_000_000

    closed = trader.check_exits(Store({"YES": {"best_bid": 0.52, "best_ask": 0.54}}), set())

    assert len(closed) == 1
    assert closed[0].exit_reason == "latency_edge_timeout"


def test_load_open_positions_from_db(mock_storage_path):
    storage = storage_v2.StorageV2(path=mock_storage_path)
    # Insert a dummy row directly
    pos = {
        "token_id": "OPEN",
        "match_id": "M1",
        "side": "NO",
        "market_name": "Test",
        "entry_price": 0.39,
        "shares": 142.05128205128204,
        "cost_usd": 55.4,
        "entry_game_time_sec": 1447,
        "entry_time_ns": 0,
        "event_type": "POLL_COMEBACK_RECOVERY",
        "lag": 0.1258,
        "expected_move": 0.1308
    }
    storage.save_position(pos, mode="paper")

    trader = PaperTrader()
    restored = trader.load_open_positions()

    assert restored == 1
    assert set(trader.positions) == {"OPEN"}
    recovered_pos = trader.positions["OPEN"]
    assert recovered_pos.match_id == "M1"
    assert recovered_pos.side == "NO"
    assert recovered_pos.entry_price == pytest.approx(0.39)
    assert recovered_pos.entry_game_time_sec == 1447
    assert trader._match_open_usd["M1"] == pytest.approx(55.4)


def test_research_mode_allows_paper_only_entry(monkeypatch):
    monkeypatch.setattr("paper_trader.PAPER_MODE", "research")
    trader = PaperTrader()
    store = Store({"YES": {"best_ask": 0.50, "best_bid": 0.48, "ask_size": 100}})

    pos, reason = trader.enter(
        signal=_signal(would_pass_live_gates=False, live_skip_reason="book_stale"),
        token_id="YES", side="YES", book_store=store,
        match_id="M1", market_name="Test", opposing_token_id="NO",
    )

    assert reason == "filled"
    assert pos is not None
    assert pos.paper_only_bypass is True
    assert pos.live_skip_reason == "book_stale"


def test_live_parity_mode_rejects_paper_only_entry(monkeypatch):
    monkeypatch.setattr("paper_trader.PAPER_MODE", "live_parity")
    trader = PaperTrader()
    store = Store({"YES": {"best_ask": 0.50, "best_bid": 0.48, "ask_size": 100}})

    pos, reason = trader.enter(
        signal=_signal(would_pass_live_gates=False, live_skip_reason="book_stale"),
        token_id="YES", side="YES", book_store=store,
        match_id="M1", market_name="Test", opposing_token_id="NO",
    )

    assert pos is None
    assert reason == "paper_live_parity_reject:book_stale"


def test_shadow_live_mode_requires_policy_allowed(monkeypatch):
    monkeypatch.setattr("paper_trader.PAPER_MODE", "shadow_live")
    trader = PaperTrader()
    store = Store({"YES": {"best_ask": 0.50, "best_bid": 0.48, "ask_size": 100}})

    pos, reason = trader.enter(
        signal=_signal(policy_allowed=False, policy_reason="spread_too_wide"),
        token_id="YES", side="YES", book_store=store,
        match_id="M1", market_name="Test", opposing_token_id="NO",
    )

    assert pos is None
    assert reason == "paper_shadow_live_reject:spread_too_wide"
