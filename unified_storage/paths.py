"""Path conventions for data_v2/.

All stream tables follow:
    data_v2/<table>/date=YYYY-MM-DD/part-<unix_ns>.parquet

The `date=` prefix is Hive-style partitioning; pyarrow.dataset and most query
engines recognize it automatically. `unix_ns` in the filename makes each
write atomic and ordered (later writes sort after earlier ones).
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

DATA_V2_ROOT = Path(os.getenv("DATA_V2_ROOT", "data_v2"))
SQLITE_PATH = DATA_V2_ROOT / "operational.db"


def partition_dir(table: str, date_utc: datetime) -> Path:
    """Directory holding parquet parts for one (table, date) partition.

    `date_utc` is interpreted in UTC; only the date portion is used.
    """
    d = date_utc.astimezone(timezone.utc).strftime("%Y-%m-%d")
    return DATA_V2_ROOT / table / f"date={d}"


def partition_file(table: str, date_utc: datetime, part_ns: int | None = None) -> Path:
    """File path for a single parquet write. Caller is responsible for
    creating the parent directory."""
    if part_ns is None:
        part_ns = time.time_ns()
    return partition_dir(table, date_utc) / f"part-{part_ns}.parquet"
