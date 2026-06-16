"""Streaming writers for live bot use.

Each `BatchWriter` accumulates rows in memory and flushes to one Parquet
file per (table, date) partition when:
  - the buffer reaches `batch_rows` rows, OR
  - `flush_interval_sec` has passed since the first row in the buffer, OR
  - `close()` is called.

Threading: each writer is single-threaded by design. If you call it from
multiple threads, wrap your own lock. The bot's loggers are already
single-writer per process.

For backfill use the `bulk` module instead.
"""
from __future__ import annotations

import time
from typing import Mapping

from .bulk import rows_to_table, write_partitioned
from .schemas import ALL_SCHEMAS


class BatchWriter:
    def __init__(
        self,
        table_name: str,
        *,
        batch_rows: int = 500,
        flush_interval_sec: float = 30.0,
        source_file: str = "live",
    ):
        if table_name not in ALL_SCHEMAS:
            raise ValueError(f"unknown table: {table_name}")
        self.table_name = table_name
        self.batch_rows = batch_rows
        self.flush_interval_sec = flush_interval_sec
        self.source_file = source_file
        self._buffer: list[Mapping] = []
        self._first_row_at: float | None = None

    def append(self, row: Mapping) -> None:
        if not self._buffer:
            self._first_row_at = time.monotonic()
        self._buffer.append(row)
        self._maybe_flush()

    def _maybe_flush(self) -> None:
        if not self._buffer:
            return
        if (
            len(self._buffer) >= self.batch_rows
            or (self._first_row_at is not None
                and time.monotonic() - self._first_row_at >= self.flush_interval_sec)
        ):
            self.flush()

    def flush(self) -> dict[str, int]:
        if not self._buffer:
            return {}
        table = rows_to_table(self._buffer, self.table_name, source_file=self.source_file)
        result = write_partitioned(table, self.table_name)
        self._buffer.clear()
        self._first_row_at = None
        return result

    def close(self) -> dict[str, int]:
        return self.flush()

    def __enter__(self) -> "BatchWriter":
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
