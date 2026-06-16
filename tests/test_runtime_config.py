from __future__ import annotations

import pytest

from runtime_config import load_config


def test_runtime_defaults_match_env_example_safety_values():
    cfg = load_config(env={}, validate_real_live=False)

    assert cfg.feed.steam_poll_seconds == 0.5
    assert cfg.feed.max_steam_age_ms == 1500
    assert cfg.book.max_book_age_ms == 750
    assert cfg.book.max_spread == 0.06
    assert cfg.signal.min_lag == 0.08
    assert cfg.signal.min_executable_edge == 0.03
    assert cfg.paper.paper_mode == "research"
    assert cfg.live.live_mode == "off"
    assert cfg.live.max_total_live_usd == 10
    assert cfg.live.max_trade_usd == 1
    assert cfg.live.max_open_positions == 1
    assert cfg.live.max_daily_drawdown_usd == 10


def test_runtime_config_tracks_env_source():
    cfg = load_config(
        env={
            "LIVE_TRADING": "true",
            "MAX_BOOK_AGE_MS": "15000",
            "DSWING_ENABLED": "true",
        },
        validate_real_live=False,
    )

    assert cfg.live.live_mode == "dry_run"
    assert cfg.book.max_book_age_ms == 15000
    assert cfg.strategy.dswing_enabled is True
    assert cfg.source_for("MAX_BOOK_AGE_MS") == "env"
    assert cfg.source_for("MAX_SPREAD") == "default"


def test_min_lag_preserves_legacy_min_edge_fallback():
    cfg = load_config(env={"MIN_EDGE": "0.12"}, validate_real_live=False)

    assert cfg.signal.min_lag == 0.12
    assert cfg.source_for("MIN_LAG") == "env"


def test_invalid_paper_mode_rejected():
    with pytest.raises(RuntimeError, match="PAPER_MODE"):
        load_config(env={"PAPER_MODE": "realish"}, validate_real_live=False)


def test_real_live_fails_closed_on_defaults():
    with pytest.raises(RuntimeError, match="real live mode requires explicit safe values"):
        load_config(env={"ENABLE_REAL_LIVE_TRADING": "true"})


def test_real_live_allows_explicit_safe_values():
    cfg = load_config(
        env={
            "ENABLE_REAL_LIVE_TRADING": "true",
            "MAX_TOTAL_LIVE_USD": "10",
            "MAX_TRADE_USD": "1",
            "MAX_OPEN_POSITIONS": "1",
            "MAX_DAILY_DRAWDOWN_USD": "10",
            "MAX_STEAM_AGE_MS": "1500",
            "MAX_SOURCE_UPDATE_AGE_SEC": "45",
            "MAX_BOOK_AGE_MS": "750",
            "MAX_SPREAD": "0.06",
            "MIN_ASK_SIZE_USD": "25",
            "MIN_LAG": "0.08",
            "MIN_EXECUTABLE_EDGE": "0.03",
        }
    )

    assert cfg.live.live_mode == "real"
