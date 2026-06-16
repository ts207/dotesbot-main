from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import dashboard
import storage_v2


def _write_csv(path: Path, headers: list[str], rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(",".join(headers) + "\n")
        for r in rows:
            f.write(",".join(str(r.get(h, "")) for h in headers) + "\n")


@pytest.fixture
def fake_logs(tmp_path, monkeypatch):
    """Point dashboard's hard-coded paths at tmp_path fixtures."""
    monkeypatch.setattr(dashboard, "LIVE_ATTEMPTS_CSV_PATH", str(tmp_path / "live_attempts.csv"))
    monkeypatch.setattr(dashboard, "LIVE_EXITS_CSV_PATH", str(tmp_path / "live_exits.csv"))
    monkeypatch.setattr(dashboard, "USDC_BALANCE_JSON_PATH", str(tmp_path / "usdc_balance.json"))
    monkeypatch.setattr(storage_v2, "DEFAULT_DB_PATH", str(tmp_path / "state_v2.sqlite"))
    return tmp_path


@pytest.mark.asyncio
async def test_live_health_filters_startup_heartbeat_from_exit_count(fake_logs):
    _write_csv(
        fake_logs / "live_exits.csv",
        ["timestamp_utc", "position_id", "reason"],
        [
            {"timestamp_utc": "2026-05-25T10:00:00.000+00:00",
             "position_id": "STARTUP_HEARTBEAT", "reason": "startup_heartbeat"},
            {"timestamp_utc": "2026-05-25T10:15:00.000+00:00",
             "position_id": "P1", "reason": "take_profit"},
        ],
    )
    _write_csv(fake_logs / "live_attempts.csv", ["timestamp_utc", "phase"], [])
    
    storage = storage_v2.StorageV2(path=str(fake_logs / "state_v2.sqlite"))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    storage.save_daily_budget(today, {"open_positions": 0})

    health = await dashboard._live_health()
    assert health["exits"] == 1  # heartbeat row excluded
    assert health["drift_count"] == 0


@pytest.mark.asyncio
async def test_live_health_detects_drift(fake_logs):
    _write_csv(fake_logs / "live_exits.csv", ["timestamp_utc", "position_id", "reason"], [])
    _write_csv(fake_logs / "live_attempts.csv", ["timestamp_utc", "phase"], [])
    
    storage = storage_v2.StorageV2(path=str(fake_logs / "state_v2.sqlite"))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    storage.save_daily_budget(today, {"open_positions": 0})
    storage.save_position({"position_id": "T1", "token_id": "T1", "state": "OPEN"}, mode="live")
    storage.save_position({"position_id": "T2", "token_id": "T2", "state": "PENDING_EXIT_GTC"}, mode="live")
    storage.save_position({"position_id": "T3", "token_id": "T3", "state": "CLOSED"}, mode="live")

    health = await dashboard._live_health()
    assert health["state_open_positions"] == 0
    assert health["store_active_positions"] == 2  # OPEN + PENDING_EXIT_GTC (CLOSED excluded)
    assert health["drift_count"] == 2


@pytest.mark.asyncio
async def test_live_health_exposes_usdc_balance_and_age(fake_logs, monkeypatch):
    import time as _time
    monkeypatch.delenv("PK", raising=False)
    monkeypatch.delenv("POLY_PRIVATE_KEY", raising=False)
    _write_csv(fake_logs / "live_attempts.csv", ["timestamp_utc", "phase"], [])
    _write_csv(fake_logs / "live_exits.csv", ["timestamp_utc", "position_id", "reason"], [])
    (fake_logs / "usdc_balance.json").write_text(
        json.dumps({"usdc_balance": 27.5, "checked_at_ns": _time.time_ns() - 3_000_000_000})
    )
    health = await dashboard._live_health()
    assert health["usdc_balance"] == 27.5
    # Age is at least 3 seconds (just wrote a ~3s-old timestamp).
    assert health["usdc_balance_age_sec"] is not None
    assert health["usdc_balance_age_sec"] >= 2


@pytest.mark.asyncio
async def test_live_health_missing_usdc_file_is_none(fake_logs, monkeypatch):
    monkeypatch.delenv("PK", raising=False)
    monkeypatch.delenv("POLY_PRIVATE_KEY", raising=False)
    _write_csv(fake_logs / "live_attempts.csv", ["timestamp_utc", "phase"], [])
    _write_csv(fake_logs / "live_exits.csv", ["timestamp_utc", "position_id", "reason"], [])
    health = await dashboard._live_health()
    assert health["usdc_balance"] == 0.0
    assert health["usdc_balance_age_sec"] is None


@pytest.mark.asyncio
async def test_live_health_filled_usd_includes_resolution_rows(fake_logs):
    _write_csv(
        fake_logs / "live_attempts.csv",
        ["timestamp_utc", "phase", "submitted_size_usd", "filled_size_usd"],
        [
            # Initial submit recorded 0 filled (delayed at submit time)
            {"timestamp_utc": "2026-05-25T10:00:00.000+00:00",
             "phase": "submit", "submitted_size_usd": "5", "filled_size_usd": "0"},
            # A1 resolution row later confirmed the fill
            {"timestamp_utc": "2026-05-25T10:00:30.000+00:00",
             "phase": "resolution", "submitted_size_usd": "5", "filled_size_usd": "5"},
        ],
    )
    _write_csv(fake_logs / "live_exits.csv", ["timestamp_utc", "position_id", "reason"], [])

    health = await dashboard._live_health()
    assert health["attempts"] == 1       # only submit phase counts as an attempt
    assert health["resolutions"] == 1
    assert health["filled_usd"] == 5.0    # resolution credit captured
    assert health["submitted_usd"] == 5.0
