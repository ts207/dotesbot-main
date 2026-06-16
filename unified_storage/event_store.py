"""Strict readers for data_v2 research replays.

These helpers make quarantine/manual-window handling explicit. A replay should
never get a "successful" dataset read by accidentally ignoring corrupt parquet
parts or manual-contaminated intervals.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as pds


NS_PER_SECOND = 1_000_000_000


@dataclass(frozen=True)
class DataFile:
    path: Path
    rel_path: str
    size_bytes: int
    quarantined: bool
    quarantine_reason: str = ""


@dataclass(frozen=True)
class ManualWindow:
    start_ns: int
    end_ns: int
    reason: str


def repo_relative(path: Path, repo_root: Path | None = None) -> str:
    base = (repo_root or Path.cwd()).resolve()
    p = path.resolve()
    try:
        return p.relative_to(base).as_posix()
    except ValueError:
        return p.as_posix()


def load_quarantine_manifest(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        rows = csv.DictReader(f)
        out: dict[str, dict[str, str]] = {}
        for row in rows:
            rel = (row.get("path") or "").replace("\\", "/").strip()
            if rel:
                out[rel] = dict(row)
        return out


def discover_parquet_files(
    table_dir: Path,
    quarantine_manifest: Path,
    repo_root: Path | None = None,
) -> list[DataFile]:
    manifest = load_quarantine_manifest(quarantine_manifest)
    files: list[DataFile] = []
    if not table_dir.exists():
        return files
    for path in sorted(table_dir.rglob("*.parquet")):
        rel = repo_relative(path, repo_root)
        q = manifest.get(rel)
        files.append(
            DataFile(
                path=path,
                rel_path=rel,
                size_bytes=path.stat().st_size,
                quarantined=q is not None,
                quarantine_reason=(q or {}).get("reason", ""),
            )
        )
    return files


def usable_parquet_paths(
    table_dir: Path,
    quarantine_manifest: Path,
    repo_root: Path | None = None,
) -> list[Path]:
    usable: list[Path] = []
    errors: list[str] = []
    for f in discover_parquet_files(table_dir, quarantine_manifest, repo_root):
        if f.quarantined:
            continue
        if f.size_bytes == 0:
            errors.append(f"{f.rel_path}: zero-byte parquet is not quarantined")
            continue
        usable.append(f.path)
    if errors:
        raise ValueError("; ".join(errors))
    return usable


def read_table(
    table_dir: Path,
    quarantine_manifest: Path,
    columns: list[str] | None = None,
    filter_expr=None,
    repo_root: Path | None = None,
) -> pa.Table:
    paths = usable_parquet_paths(table_dir, quarantine_manifest, repo_root)
    if not paths:
        return pa.table({})
    dataset = pds.dataset([str(p) for p in paths], format="parquet")
    available = set(dataset.schema.names)
    selected = [c for c in (columns or dataset.schema.names) if c in available]
    if not selected:
        return pa.table({})
    return dataset.to_table(columns=selected, filter=filter_expr)


def parse_ns(value: str | int | float | None) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            return int(text)
    except ValueError:
        return None
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(dt.timestamp() * NS_PER_SECOND)


def load_manual_windows(path: Path) -> list[ManualWindow]:
    if not path.exists():
        return []
    windows: list[ManualWindow] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            start = parse_ns(row.get("start_ts"))
            end = parse_ns(row.get("end_ts"))
            if start is None or end is None:
                continue
            windows.append(ManualWindow(start, end, row.get("reason") or "manual_excluded"))
    return sorted(windows, key=lambda w: w.start_ns)


def manual_window_reason(decision_ns: int | None, windows: Iterable[ManualWindow]) -> str | None:
    if decision_ns is None:
        return None
    for window in windows:
        if window.start_ns <= decision_ns <= window.end_ns:
            return window.reason
    return None


def latest_ts_at_or_before(table: pa.Table, ts_column: str, decision_ns: int | None) -> int | None:
    if table.num_rows == 0 or decision_ns is None or ts_column not in table.column_names:
        return None
    filtered = table.filter(pc.less_equal(pc.field(ts_column), decision_ns))
    if filtered.num_rows == 0:
        return None
    value = pc.max(filtered[ts_column]).as_py()
    return int(value) if value is not None else None


def table_rows(table: pa.Table) -> list[dict]:
    if table.num_rows == 0:
        return []
    return table.to_pylist()
