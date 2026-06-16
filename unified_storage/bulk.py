"""Bulk-write helpers used by the backfill script.

For streaming (the live bot path) use the writers in `writers.py` —
those keep a per-table batch buffer and flush on size/time thresholds.
"""
from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

import pyarrow as pa
import pyarrow.parquet as pq

from .schemas import ALL_SCHEMAS, SCHEMA_VERSION
from .paths import partition_file, partition_dir


def _coerce_value(value, pa_type: pa.DataType):
    """Best-effort coercion of a CSV string into the schema type. Empty
    strings, 'None', 'nan' map to None. We deliberately swallow conversion
    errors here and return None — backfill from messy CSVs should never
    crash on a single bad row."""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        if s == "" or s.lower() in {"none", "nan", "null"}:
            return None
        if pa.types.is_integer(pa_type):
            try:
                # Allow "12.0" → 12.
                return int(float(s))
            except (TypeError, ValueError):
                return None
        if pa.types.is_floating(pa_type):
            try:
                return float(s)
            except (TypeError, ValueError):
                return None
        if pa.types.is_boolean(pa_type):
            return s.lower() in {"true", "1", "yes", "t"}
        return s
    # Already a non-string scalar. pyarrow's pa.array(..., type=...) does NOT
    # auto-cast a Python bool/int into a string column (raises "Expected bytes,
    # got a 'bool'/'int' object"), so stringify explicitly for string columns.
    if pa.types.is_string(pa_type) or pa.types.is_large_string(pa_type):
        return str(value)
    # bool → int for numeric columns (pyarrow treats bool as a distinct type).
    if isinstance(value, bool) and (pa.types.is_integer(pa_type) or pa.types.is_floating(pa_type)):
        return int(value)
    return value


def rows_to_table(rows: Iterable[Mapping], table_name: str, source_file: str = "") -> pa.Table:
    """Convert an iterable of dict rows into a pyarrow Table that matches
    the schema for `table_name`. Missing columns are filled with None.
    `source_file` is stamped on every row for provenance.

    Column aliases — Phase 2 normalization: CSVs historically wrote
    `timestamp_utc` while schemas use `received_at_utc`. When the schema
    column is missing in the row, we transparently fall back to the
    alias. `received_at_ns` is derived from a timestamp string when
    absent. These aliases mean per-logger `_to_parquet_row` overrides are
    no longer needed just for column renaming.
    """
    schema = ALL_SCHEMAS[table_name]
    field_names = [f.name for f in schema]
    columns: dict[str, list] = {name: [] for name in field_names}
    n = 0
    for row in rows:
        # Resolve canonical timestamp once per row.
        ts_str = row.get("received_at_utc") or row.get("timestamp_utc")
        ts_ns = row.get("received_at_ns")
        if ts_ns in (None, "") and ts_str:
            try:
                ts_ns = int(datetime.fromisoformat(str(ts_str)).timestamp() * 1_000_000_000)
            except (TypeError, ValueError):
                ts_ns = None
        for f in schema:
            name = f.name
            if name == "schema_version":
                columns[name].append(SCHEMA_VERSION)
            elif name == "source_file":
                columns[name].append(source_file)
            elif name == "received_at_utc" and not row.get(name):
                columns[name].append(ts_str)
            elif name == "received_at_ns" and not row.get(name):
                columns[name].append(ts_ns)
            elif name == "date":
                # Derive date partition from received_at_ns when present;
                # otherwise leave None and the partitioner downstream
                # will reject it.
                ts_ns = row.get("received_at_ns")
                if ts_ns:
                    try:
                        dt = datetime.fromtimestamp(int(ts_ns) / 1e9, tz=timezone.utc)
                        columns[name].append(dt.strftime("%Y-%m-%d"))
                    except (TypeError, ValueError, OSError):
                        columns[name].append(None)
                else:
                    # Try received_at_utc as fallback.
                    ts_str = row.get("received_at_utc") or row.get("timestamp_utc")
                    if ts_str:
                        try:
                            dt = datetime.fromisoformat(str(ts_str))
                            columns[name].append(dt.astimezone(timezone.utc).strftime("%Y-%m-%d"))
                        except (TypeError, ValueError):
                            columns[name].append(None)
                    else:
                        columns[name].append(None)
            else:
                raw = row.get(name)
                columns[name].append(_coerce_value(raw, f.type))
        n += 1
    arrays = [pa.array(columns[f.name], type=f.type) for f in schema]
    return pa.Table.from_arrays(arrays, schema=schema)


def write_partitioned(table: pa.Table, table_name: str) -> dict[str, int]:
    """Split `table` by the `date` column and write one parquet file per
    date partition. Returns a {date: rows_written} map.

    Uses `pyarrow.parquet.write_table` per-partition rather than the
    Hive-partition dataset writer so we can control the file naming
    (part-<ns>.parquet for natural ordering)."""
    if "date" not in table.column_names:
        raise ValueError("table missing `date` column — cannot partition")
    if table.num_rows == 0:
        return {}
    date_col = table.column("date").to_pylist()
    by_date: dict[str, list[int]] = defaultdict(list)
    for i, d in enumerate(date_col):
        if d is None:
            continue
        by_date[d].append(i)
    written: dict[str, int] = {}
    for d, indices in by_date.items():
        sub = table.take(pa.array(indices, type=pa.int64()))
        # Parse the date back to a UTC datetime for the partition path.
        try:
            dt = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        # Strip the `date` column before writing: the Hive partition path
        # provides it on read. Keeping it inside the file conflicts with
        # pyarrow's dataset reader (it infers `date` as a dictionary type
        # from the path but the file column is plain string → merge error).
        sub_for_write = sub.drop(["date"])
        out_path = partition_file(table_name, dt)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(sub_for_write, str(out_path), compression="zstd", compression_level=3)
        written[d] = sub.num_rows
    return written
