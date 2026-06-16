"""Unified storage layer (Phase 1 of data consolidation).

Stream tables live in `data_v2/<table>/date=YYYY-MM-DD/part-*.parquet`.
State tables live in `data_v2/operational.db` (SQLite).

Top-level imports re-export the writer classes and state-table helpers so
callers can do `from unified_storage import SnapshotWriter`.
"""
from .schemas import (
    SCHEMA_SNAPSHOTS,
    SCHEMA_BOOK_TICKS,
    SCHEMA_DOTA_EVENTS,
    SCHEMA_SIGNALS,
    SCHEMA_TRADE_ATTEMPTS,
    SCHEMA_EXITS,
    SCHEMA_MARKOUTS,
    SCHEMA_CONTINUOUS_ATTEMPTS,
    SCHEMA_ARB_ATTEMPTS,
    SCHEMA_SOURCE_DELAY,
    SCHEMA_LATENCY,
    SCHEMA_VERSION,
    ALL_SCHEMAS,
)
from .paths import DATA_V2_ROOT, partition_dir, partition_file
from .writers import BatchWriter
