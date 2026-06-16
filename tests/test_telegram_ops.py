from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import telegram_ops  # noqa: E402


def _attempt_csv(path: Path, rows: list[dict]):
    headers = [
        "timestamp_utc", "phase", "event_type", "submitted_size_usd",
        "filled_size_usd", "order_status",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(",".join(headers) + "\n")
        for r in rows:
            f.write(",".join(str(r.get(h, "")) for h in headers) + "\n")


def _exit_csv(path: Path, rows: list[dict]):
    headers = ["timestamp_utc", "position_id", "reason"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(",".join(headers) + "\n")
        for r in rows:
            f.write(",".join(str(r.get(h, "")) for h in headers) + "\n")


def _args(**overrides):
    base = dict(
        live_attempts="logs/live_attempts.csv",
        live_exits="logs/live_exits.csv",
        state="logs/telegram_ops_state.json",
        max_idle_hours=6.0,
        hours=24.0,
        window_active=False,
        require_send=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_liveness_returns_none_when_window_inactive(tmp_path, monkeypatch):
    monkeypatch.delenv("DREAMLEAGUE_ACTIVE", raising=False)
    args = _args(
        live_attempts=str(tmp_path / "live_attempts.csv"),
        state=str(tmp_path / "state.json"),
    )
    # Even with no attempts file, inactive window → no alert.
    assert telegram_ops.liveness_alert(args) is None


def test_liveness_returns_none_when_recent_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("DREAMLEAGUE_ACTIVE", "true")
    p = tmp_path / "live_attempts.csv"
    recent = datetime.now(timezone.utc) - timedelta(minutes=10)
    _attempt_csv(p, [{"timestamp_utc": recent.isoformat(timespec="milliseconds")}])
    args = _args(live_attempts=str(p), state=str(tmp_path / "state.json"))
    assert telegram_ops.liveness_alert(args) is None


def test_liveness_fires_when_stale_and_window_active(tmp_path):
    p = tmp_path / "live_attempts.csv"
    stale = datetime.now(timezone.utc) - timedelta(hours=12)
    _attempt_csv(p, [{"timestamp_utc": stale.isoformat(timespec="milliseconds")}])
    state = tmp_path / "state.json"
    args = _args(
        live_attempts=str(p),
        state=str(state),
        window_active=True,
        max_idle_hours=6.0,
    )
    text = telegram_ops.liveness_alert(args)
    assert text is not None
    assert "12." in text  # the rendered age
    # Dedup: second call same day returns None
    assert telegram_ops.liveness_alert(args) is None
    # State file recorded today's date
    payload = json.loads(state.read_text(encoding="utf-8"))
    assert "liveness_alert_date" in payload


def test_liveness_fires_when_no_attempts_file_ever(tmp_path):
    args = _args(
        live_attempts=str(tmp_path / "missing.csv"),
        state=str(tmp_path / "state.json"),
        window_active=True,
    )
    text = telegram_ops.liveness_alert(args)
    assert text is not None
    assert "Last attempt: never" in text


def test_daily_summary_aggregates_submit_rows(tmp_path):
    now = datetime.now(timezone.utc)
    recent_iso = now.isoformat(timespec="milliseconds")
    old_iso = (now - timedelta(hours=48)).isoformat(timespec="milliseconds")
    rows = [
        {"timestamp_utc": recent_iso, "phase": "submit",
         "event_type": "POLL_FIGHT_SWING", "submitted_size_usd": "5",
         "filled_size_usd": "5", "order_status": "matched"},
        {"timestamp_utc": recent_iso, "phase": "submit",
         "event_type": "POLL_FIGHT_SWING", "submitted_size_usd": "5",
         "filled_size_usd": "0", "order_status": "delayed"},
        {"timestamp_utc": recent_iso, "phase": "resolution",
         "event_type": "POLL_FIGHT_SWING", "submitted_size_usd": "5",
         "filled_size_usd": "5", "order_status": "filled"},
        # Out-of-window row must be excluded.
        {"timestamp_utc": old_iso, "phase": "submit",
         "event_type": "POLL_FIGHT_SWING", "submitted_size_usd": "100",
         "filled_size_usd": "100", "order_status": "matched"},
    ]
    attempts_p = tmp_path / "live_attempts.csv"
    _attempt_csv(attempts_p, rows)
    exits_p = tmp_path / "live_exits.csv"
    _exit_csv(exits_p, [{"timestamp_utc": recent_iso, "position_id": "X",
                         "reason": "take_profit"}])
    args = _args(
        live_attempts=str(attempts_p),
        live_exits=str(exits_p),
        hours=24.0,
    )
    text = telegram_ops.daily_summary(args)
    # Submit-phase rows only: 2 submits totaling $10 submitted, $5 filled.
    assert "Signals attempted: 2" in text
    assert "Submitted: $10.00" in text
    assert "Confirmed filled: $5.00" in text
    assert "Exit rows: 1" in text
    assert "POLL_FIGHT_SWING:$5.00" in text


def test_send_telegram_noops_without_env(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert telegram_ops.send_telegram("hi") is False
    assert "hi" in capsys.readouterr().out


def test_send_telegram_raises_when_required(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(SystemExit):
        telegram_ops.send_telegram("hi", require_send=True)
