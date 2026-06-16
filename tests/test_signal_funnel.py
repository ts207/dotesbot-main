from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "signal_funnel.py"


def _write_attempts(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(body).lstrip(), encoding="utf-8")


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, check=True,
    )


def test_funnel_groups_rejects_by_reason_prefix(tmp_path):
    csv_path = tmp_path / "live_attempts.csv"
    _write_attempts(csv_path, """
        timestamp_utc,phase,event_type,order_status,reason_if_rejected
        2026-05-25T10:00:00.000+00:00,submit,POLL_FIGHT_SWING,rejected_precheck,event_quality_too_low:q=0.200_min=0.500
        2026-05-25T10:00:05.000+00:00,submit,POLL_FIGHT_SWING,rejected_precheck,event_quality_too_low:q=0.350_min=0.500
        2026-05-25T10:00:10.000+00:00,submit,POLL_FIGHT_SWING,rejected_precheck,edge_too_small:edge=0.0200_min=0.0500
        2026-05-25T10:00:15.000+00:00,submit,POLL_FIGHT_SWING,delayed,
        2026-05-25T10:00:20.000+00:00,submit,POLL_DECISIVE_STOMP,rejected_precheck,event_quality_too_low:q=0.300_min=0.500
    """)
    result = _run(["--live-attempts", str(csv_path), "--since", "2026-05-25T00:00:00+00:00"])
    out = result.stdout
    assert "POLL_FIGHT_SWING: 4 signals" in out
    assert "POLL_DECISIVE_STOMP: 1 signals" in out
    # Two event_quality_too_low rows for fight_swing → grouped under one prefix
    assert "2 ( 50.0%)  event_quality_too_low" in out
    assert "1 ( 25.0%)  edge_too_small" in out


def test_funnel_event_filter_and_verbose(tmp_path):
    csv_path = tmp_path / "live_attempts.csv"
    _write_attempts(csv_path, """
        timestamp_utc,phase,event_type,order_status,reason_if_rejected
        2026-05-25T10:00:00.000+00:00,submit,POLL_FIGHT_SWING,rejected_precheck,event_quality_too_low:q=0.200_min=0.500
        2026-05-25T10:00:01.000+00:00,submit,POLL_DECISIVE_STOMP,rejected_precheck,event_quality_too_low:q=0.111_min=0.500
    """)
    result = _run([
        "--live-attempts", str(csv_path),
        "--event", "POLL_DECISIVE_STOMP",
        "--verbose",
        "--since", "2026-05-25T00:00:00+00:00",
    ])
    out = result.stdout
    assert "POLL_FIGHT_SWING" not in out
    assert "POLL_DECISIVE_STOMP: 1 signals" in out
    # Verbose mode prints the full reason string with values
    assert "q=0.111" in out


def test_funnel_handles_missing_file(tmp_path):
    result = _run(["--live-attempts", str(tmp_path / "does_not_exist.csv")])
    assert "missing" in result.stdout


def test_funnel_skips_resolution_phase_rows(tmp_path):
    """A1 resolution rows shouldn't double-count as signals attempted."""
    csv_path = tmp_path / "live_attempts.csv"
    _write_attempts(csv_path, """
        timestamp_utc,phase,event_type,order_status,reason_if_rejected
        2026-05-25T10:00:00.000+00:00,submit,POLL_FIGHT_SWING,delayed,
        2026-05-25T10:00:30.000+00:00,resolution,POLL_FIGHT_SWING,filled,
    """)
    result = _run(["--live-attempts", str(csv_path), "--since", "2026-05-25T00:00:00+00:00"])
    assert "POLL_FIGHT_SWING: 1 signals" in result.stdout
