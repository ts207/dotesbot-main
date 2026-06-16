from pathlib import Path

import pytest

from unified_storage.event_store import load_manual_windows, usable_parquet_paths


def test_zero_byte_parquet_requires_quarantine(tmp_path: Path):
    table_dir = tmp_path / "data_v2" / "snapshots" / "date=2026-06-05"
    table_dir.mkdir(parents=True)
    bad = table_dir / "part-1.parquet"
    bad.write_bytes(b"")
    manifest = tmp_path / "data_v2" / "quarantine_manifest.csv"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("path,reason,detected_ts,action\n", encoding="utf-8")

    with pytest.raises(ValueError, match="zero-byte parquet is not quarantined"):
        usable_parquet_paths(table_dir, manifest, tmp_path)

    manifest.write_text(
        "path,reason,detected_ts,action\n"
        "data_v2/snapshots/date=2026-06-05/part-1.parquet,zero_byte_parquet,2026-06-07T00:00:00+00:00,skip\n",
        encoding="utf-8",
    )

    assert usable_parquet_paths(table_dir, manifest, tmp_path) == []


def test_manual_windows_parse_iso_timestamps(tmp_path: Path):
    path = tmp_path / "excluded_time_windows.csv"
    path.write_text(
        "start_ts,end_ts,reason\n"
        "2026-06-07T00:00:00+00:00,2026-06-07T00:01:00+00:00,manual_cockpit_trading\n",
        encoding="utf-8",
    )

    windows = load_manual_windows(path)

    assert len(windows) == 1
    assert windows[0].reason == "manual_cockpit_trading"
    assert windows[0].end_ns > windows[0].start_ns
