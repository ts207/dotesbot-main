import glob
import os
import time
import csv

import storage
from storage import CsvLogger, LiveExitLogger, LiveLeagueRawLogger

def test_csv_logger_flush(tmp_path):
    log_file = tmp_path / "test_flush.csv"
    headers = ["ts", "val"]
    
    logger = CsvLogger(str(log_file), headers)
    
    # Append many rows quickly
    for i in range(100):
        logger.append({"ts": time.time(), "val": i})
    
    # Stop the logger, which should wait for the worker thread to finish
    logger.stop()
    
    # Verify all rows are written
    assert log_file.exists()
    with open(log_file, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert len(rows) == 100
        for i, row in enumerate(rows):
            assert int(row["val"]) == i


def test_liveleague_logger_rotates_when_size_exceeded(tmp_path, monkeypatch):
    """When the active jsonl exceeds the threshold, it should be renamed and
    the renamed file gzipped in the background."""
    log_file = tmp_path / "live.jsonl"
    monkeypatch.setattr(storage, "LIVELEAGUE_ROTATE_BYTES", 1024)  # 1 KB
    monkeypatch.setattr(LiveLeagueRawLogger, "_SIZE_CHECK_EVERY", 5)

    logger = LiveLeagueRawLogger(str(log_file))
    # ~120 bytes per row × 50 → ~6 KB, well past 1 KB.
    payload = {"match_id": "X" * 50}
    for _ in range(50):
        logger.log_raw(payload, received_at_ns=time.time_ns())

    # Wait briefly for background gzip to finish.
    for _ in range(50):
        rotated_gz = glob.glob(str(tmp_path / "live.jsonl.*.gz"))
        if rotated_gz:
            break
        time.sleep(0.05)

    rotated_gz = glob.glob(str(tmp_path / "live.jsonl.*.gz"))
    assert rotated_gz, "expected at least one .gz rotated file"
    # Original file should still exist (post-rotation writes go to it).
    assert log_file.exists()
    # Gzipped output must contain actual data (not a zero-byte placeholder).
    import gzip as _gz
    with _gz.open(rotated_gz[0], "rb") as f:
        assert len(f.read(64)) > 0


def _drain_csv(path):
    with open(path, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_live_exit_logger_startup_heartbeat(tmp_path):
    log = LiveExitLogger(str(tmp_path / "exits.csv"))
    log.log_startup_heartbeat(code_version="abc1234")
    log.stop()
    rows = _drain_csv(tmp_path / "exits.csv")
    assert len(rows) == 1
    assert rows[0]["position_id"] == "STARTUP_HEARTBEAT"
    assert rows[0]["reason"] == "startup_heartbeat"
    assert rows[0]["reason_if_rejected"] == "abc1234"
    assert rows[0]["order_status"] == "lifecycle"


def test_live_exit_logger_lifecycle_row(tmp_path):
    class _Pos:
        position_id = "P1"
        token_id = "T1"
        match_id = "M1"
        shares = 10.0
    log = LiveExitLogger(str(tmp_path / "exits.csv"))
    log.log_lifecycle(position=_Pos(), event="entry_zero_fill_cleanup",
                      raw_response_json='{"x":1}')
    log.stop()
    rows = _drain_csv(tmp_path / "exits.csv")
    assert len(rows) == 1
    assert rows[0]["position_id"] == "P1"
    assert rows[0]["token_id"] == "T1"
    assert rows[0]["match_id"] == "M1"
    assert rows[0]["reason"] == "entry_zero_fill_cleanup"
    assert rows[0]["shares_requested"] == "10.0"
    assert rows[0]["order_status"] == "lifecycle"
    assert rows[0]["raw_response_json"] == '{"x":1}'
