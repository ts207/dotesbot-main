from __future__ import annotations

import csv
import time

import decisive_swing_engine as dse
from decisive_swing_engine import DecisiveSwingEngine, DSwingReject, DSwingSignal
from storage import DSwingAttemptLogger


class FakeBookStore:
    def __init__(self, books=None):
        self._books = books or {}

    def get(self, token_id):
        return self._books.get(token_id)


def _game(**overrides):
    game = {
        "data_source": "top_live",
        "received_at_ns": time.time_ns(),
        "match_id": "8850000000",
        "game_time_sec": 900,
        "radiant_lead": 9000,
        "radiant_score": 22,
        "dire_score": 9,
        "building_state": 2047,
        "tower_state": 2047,
        "radiant_team": "Radiant Test",
        "dire_team": "Dire Test",
        "radiant_team_id": 101,
        "dire_team_id": 202,
    }
    game.update(overrides)
    return game


def _mapping(**overrides):
    mapping = {
        "name": "Radiant Test vs Dire Test",
        "market_type": "MATCH_WINNER",
        "steam_side_mapping": "normal",
        "yes_token_id": "YES",
        "no_token_id": "NO",
        "current_game_number": 1,
        "series_score_yes": 0,
        "series_score_no": 0,
    }
    mapping.update(overrides)
    return mapping


def _configure_shadow(monkeypatch, *, shadow=True, enabled=False):
    monkeypatch.setattr(dse, "DSWING_SHADOW_ENABLED", shadow)
    monkeypatch.setattr(dse, "DSWING_ENABLED", enabled)
    monkeypatch.setattr(dse, "DSWING_LEAD", 6000)
    monkeypatch.setattr(dse, "DSWING_MIN_EDGE", 0.05)
    monkeypatch.setattr(dse, "DSWING_MAX_PRICE", 0.92)
    monkeypatch.setattr(dse, "DSWING_MAX_BOOK_AGE_MS", 15000)
    dse._sniped.clear()


def test_dswing_shadow_evaluates_without_arming_trading(monkeypatch):
    _configure_shadow(monkeypatch, shadow=True, enabled=False)
    store = FakeBookStore({
        "YES": {
            "best_ask": 0.10,
            "best_bid": 0.09,
            "received_at_ns": time.time_ns(),
        }
    })

    results = DecisiveSwingEngine().evaluate(_game(), _mapping(), store)

    assert len(results) == 1
    assert isinstance(results[0], DSwingSignal)
    assert results[0].token_id == "YES"
    assert results[0].edge > 0.05


def test_dswing_stays_silent_when_shadow_and_trading_are_off(monkeypatch):
    _configure_shadow(monkeypatch, shadow=False, enabled=False)

    results = DecisiveSwingEngine().evaluate(_game(), _mapping(), FakeBookStore())

    assert results == []


def test_dswing_shadow_logs_reject_context(monkeypatch):
    _configure_shadow(monkeypatch, shadow=True, enabled=False)

    results = DecisiveSwingEngine().evaluate(
        _game(radiant_lead=2500),
        _mapping(),
        FakeBookStore(),
    )

    assert len(results) == 1
    assert isinstance(results[0], DSwingReject)
    assert results[0].reason == "lead_too_small"
    assert results[0].lead == 2500
    assert results[0].game_time_sec == 900


def test_dswing_attempt_logger_writes_signal_and_reject(tmp_path):
    logger = DSwingAttemptLogger(filename=str(tmp_path / "dswing_attempts.csv"))
    logger.log_signal(
        DSwingSignal(
            signal_id="sig1",
            match_id="m1",
            received_at_ns=1_700_000_000_000_000_000,
            direction="radiant",
            side="YES",
            token_id="YES",
            lead=9000,
            game_time_sec=900,
            p_game=0.95,
            series_fair=0.72,
            ask=0.55,
            edge=0.17,
            sized_usd=5.0,
            fair_price=0.72,
            book_age_ms=100,
        ),
        mapping=_mapping(),
    )
    logger.log_reject(
        DSwingReject(
            match_id="m2",
            reason="price_too_high",
            received_at_ns=1_700_000_000_000_000_001,
            direction="dire",
            side="NO",
            token_id="NO",
            lead=-8500,
            game_time_sec=1100,
            ask=0.95,
            book_age_ms=50,
        ),
        mapping=_mapping(),
    )
    logger.stop()

    with open(tmp_path / "dswing_attempts.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 2
    assert rows[0]["would_trade"] == "True"
    assert rows[0]["market_type"] == "MATCH_WINNER"
    assert rows[1]["would_trade"] == "False"
    assert rows[1]["reject_reason"] == "price_too_high"
