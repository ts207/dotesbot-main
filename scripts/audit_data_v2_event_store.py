#!/usr/bin/env python3
"""Audit data_v2 parquet tables before reaction-lag replay."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.dataset as pds

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from unified_storage.event_store import discover_parquet_files, usable_parquet_paths
from unified_storage.schemas import ALL_SCHEMAS


def write_csv(path: Path, rows: list[dict], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({h: row.get(h) for h in headers})


def audit_table(data_root: Path, table_name: str, quarantine_manifest: Path) -> tuple[dict, list[dict], list[dict]]:
    table_dir = data_root / table_name
    files = discover_parquet_files(table_dir, quarantine_manifest, REPO_ROOT)
    zero_rows = [
        {
            "path": f.rel_path,
            "table": table_name,
            "size_bytes": f.size_bytes,
            "quarantined": f.quarantined,
            "quarantine_reason": f.quarantine_reason,
        }
        for f in files
        if f.size_bytes == 0
    ]

    non_quarantined_zero = [r["path"] for r in zero_rows if not r["quarantined"]]
    usable = usable_parquet_paths(table_dir, quarantine_manifest, REPO_ROOT)

    rows = 0
    actual_columns: list[str] = []
    read_error = None
    if usable:
        try:
            dataset = pds.dataset([str(p) for p in usable], format="parquet")
            rows = dataset.count_rows()
            actual_columns = list(dataset.schema.names)
        except Exception as exc:  # pragma: no cover - exercised by corrupt local data
            read_error = f"{type(exc).__name__}: {exc}"

    expected_columns = list(ALL_SCHEMAS.get(table_name, []))
    expected_names = [field.name for field in expected_columns]
    missing = sorted(set(expected_names) - set(actual_columns))
    unexpected = sorted(set(actual_columns) - set(expected_names))

    schema_rows = []
    for name in expected_names:
        schema_rows.append(
            {
                "table": table_name,
                "column": name,
                "expected": True,
                "present": name in actual_columns,
                "status": "ok" if name in actual_columns else "missing",
            }
        )
    for name in unexpected:
        schema_rows.append(
            {
                "table": table_name,
                "column": name,
                "expected": False,
                "present": True,
                "status": "unexpected",
            }
        )

    summary = {
        "table": table_name,
        "exists": table_dir.exists(),
        "file_count": len(files),
        "usable_file_count": len(usable),
        "quarantined_file_count": sum(1 for f in files if f.quarantined),
        "zero_byte_file_count": len(zero_rows),
        "non_quarantined_zero_byte_files": non_quarantined_zero,
        "row_count": rows,
        "expected_column_count": len(expected_names),
        "actual_column_count": len(actual_columns),
        "missing_expected_columns": missing,
        "unexpected_columns": unexpected,
        "read_error": read_error,
    }
    return summary, schema_rows, zero_rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default="data_v2", type=Path)
    parser.add_argument("--quarantine-manifest", default="data_v2/quarantine_manifest.csv", type=Path)
    parser.add_argument("--report", default="reports/data_v2_event_store_audit.json", type=Path)
    parser.add_argument("--schema-report", default="reports/data_v2_schema_audit.csv", type=Path)
    parser.add_argument("--zero-byte-report", default="reports/data_v2_zero_byte_files.csv", type=Path)
    args = parser.parse_args()

    data_root = args.data_root
    tables = sorted(set(ALL_SCHEMAS) | {p.name for p in data_root.iterdir() if p.is_dir()})

    table_summaries: list[dict] = []
    schema_rows: list[dict] = []
    zero_rows: list[dict] = []
    for table in tables:
        summary, table_schema_rows, table_zero_rows = audit_table(data_root, table, args.quarantine_manifest)
        table_summaries.append(summary)
        schema_rows.extend(table_schema_rows)
        zero_rows.extend(table_zero_rows)

    hard_failures = []
    for table in table_summaries:
        hard_failures.extend(
            f"{table['table']}: {path}" for path in table["non_quarantined_zero_byte_files"]
        )
        if table["read_error"]:
            hard_failures.append(f"{table['table']}: {table['read_error']}")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_root": str(data_root),
        "quarantine_manifest": str(args.quarantine_manifest),
        "status": "fail" if hard_failures else "pass",
        "hard_failures": hard_failures,
        "tables": table_summaries,
    }

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_csv(args.schema_report, schema_rows, ["table", "column", "expected", "present", "status"])
    write_csv(
        args.zero_byte_report,
        zero_rows,
        ["path", "table", "size_bytes", "quarantined", "quarantine_reason"],
    )

    print(f"wrote {args.report}")
    print(f"wrote {args.schema_report}")
    print(f"wrote {args.zero_byte_report}")
    if hard_failures:
        print("hard failures:")
        for failure in hard_failures:
            print(f"  {failure}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
