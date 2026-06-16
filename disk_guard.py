from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DISK_GUARD_MIN_FREE_BYTES = int(float(os.getenv("DISK_GUARD_MIN_FREE_GB", "2")) * 1024 * 1024 * 1024)
DISK_GUARD_PATH = os.getenv("DISK_GUARD_PATH", "/")
DISK_GUARD_CHECK_INTERVAL_SEC = float(os.getenv("DISK_GUARD_CHECK_INTERVAL_SEC", "3600"))


@dataclass
class DiskGuardStatus:
    ok: bool
    path: str
    free_bytes: int
    min_free_bytes: int
    checked_at_ns: int

    @property
    def free_gb(self) -> float:
        return self.free_bytes / (1024 ** 3)

    @property
    def min_free_gb(self) -> float:
        return self.min_free_bytes / (1024 ** 3)


class DiskGuard:
    def __init__(
        self,
        *,
        path: str = DISK_GUARD_PATH,
        min_free_bytes: int = DISK_GUARD_MIN_FREE_BYTES,
        check_interval_sec: float = DISK_GUARD_CHECK_INTERVAL_SEC,
    ):
        self.path = path
        self.min_free_bytes = min_free_bytes
        self.check_interval_sec = check_interval_sec
        self._last: DiskGuardStatus | None = None

    def check(self, *, force: bool = False) -> DiskGuardStatus:
        now = time.time_ns()
        if (
            not force
            and self._last is not None
            and (now - self._last.checked_at_ns) / 1e9 < self.check_interval_sec
        ):
            return self._last
        usage = shutil.disk_usage(self.path)
        status = DiskGuardStatus(
            ok=usage.free >= self.min_free_bytes,
            path=self.path,
            free_bytes=usage.free,
            min_free_bytes=self.min_free_bytes,
            checked_at_ns=now,
        )
        self._last = status
        if not status.ok:
            logger.error(
                "disk guard HALT: path=%s free=%.2fGB min=%.2fGB",
                status.path,
                status.free_gb,
                status.min_free_gb,
            )
        return status

    def reject_reason(self) -> str | None:
        status = self.check()
        if status.ok:
            return None
        return f"disk_guard_low_free_space:free_gb={status.free_gb:.2f}_min_gb={status.min_free_gb:.2f}"
