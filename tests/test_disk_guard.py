from __future__ import annotations

from collections import namedtuple

from disk_guard import DiskGuard

Usage = namedtuple("usage", "total used free")


def test_disk_guard_ok_when_free_space_above_threshold(monkeypatch):
    monkeypatch.setattr("disk_guard.shutil.disk_usage", lambda _path: Usage(100, 10, 90))
    guard = DiskGuard(path="/", min_free_bytes=50, check_interval_sec=3600)
    status = guard.check(force=True)
    assert status.ok is True
    assert guard.reject_reason() is None


def test_disk_guard_reject_reason_when_free_space_below_threshold(monkeypatch):
    monkeypatch.setattr("disk_guard.shutil.disk_usage", lambda _path: Usage(100, 95, 5))
    guard = DiskGuard(path="/", min_free_bytes=50, check_interval_sec=3600)
    reason = guard.reject_reason()
    assert reason is not None
    assert reason.startswith("disk_guard_low_free_space")
