#!/usr/bin/env python3
"""Audit GetTopLive building/tower state transitions from raw_snapshots.csv.

This is research evidence, not a trading strategy. GetTopLive building_state is
only validated for lane-tower progress decoding; raw rax/base bits stay excluded
from live trading until this audit has stronger historical support.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from structure_state import decode_structure_state, diff_structure_state


RAW_PATH = REPO_ROOT / "logs" / "raw_snapshots.csv"
REPORT_JSON = REPO_ROOT / "reports" / "gettoplive_structure_state_audit.json"
REPORT_MD = REPO_ROOT / "reports" / "gettoplive_structure_state_audit.md"


def _tail_lines(path: Path, max_lines: int, max_bytes: int = 64 * 1024 * 1024) -> list[str]:
    if not path.exists() or max_lines <= 0:
        return []
    file_size = path.stat().st_size
    with path.open("rb") as f:
        header = f.readline().decode("utf-8", errors="replace").rstrip("\r\n")
        if not header:
            return []
        read_size = min(file_size, max_bytes)
        f.seek(max(0, file_size - read_size))
        blob = f.read(read_size).decode("utf-8", errors="replace")
    lines = blob.splitlines()
    if file_size > read_size and lines:
        lines = lines[1:]
    return [header] + lines[-max_lines:]


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _present(row: dict[str, str], field: str) -> bool:
    return str(row.get(field) or "").strip() != ""


def _structure_snapshot(row: dict[str, str]) -> dict[str, Any]:
    snap: dict[str, Any] = {
        "match_id": row.get("match_id"),
        "game_time_sec": row.get("game_time_sec"),
        "building_state": row.get("building_state"),
        "tower_state": row.get("tower_state"),
    }
    if row.get("data_source") == "top_live":
        snap["building_state_schema"] = "top_live_lane_tower_progress"
    return snap


def summarize_rows(rows: list[dict[str, str]]) -> dict[str, Any]:
    top_rows = [row for row in rows if row.get("data_source") == "top_live"]
    by_match: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in top_rows:
        match_id = str(row.get("match_id") or "")
        if match_id:
            by_match[match_id].append(row)

    required_fields = [
        "received_at_ns",
        "match_id",
        "game_time_sec",
        "radiant_lead",
        "radiant_score",
        "dire_score",
        "building_state",
        "tower_state",
    ]
    missing_required = {
        field: sum(1 for row in top_rows if not _present(row, field))
        for field in required_fields
    }

    totals = {
        "top_live_rows": len(top_rows),
        "top_live_matches": len(by_match),
        "building_state_changes": 0,
        "tower_state_changes": 0,
        "building_change_without_tower_change": 0,
        "valid_tower_deltas": 0,
        "invalid_structure_deltas": 0,
        "tower_count_increases": 0,
        "missing_required": missing_required,
    }
    examples: list[dict[str, Any]] = []
    match_summaries: dict[str, dict[str, Any]] = {}

    for match_id, match_rows in by_match.items():
        ordered = sorted(
            match_rows,
            key=lambda row: (_to_int(row.get("game_time_sec")) or -1, _to_int(row.get("received_at_ns")) or 0),
        )
        match_summary = {
            "rows": len(ordered),
            "building_state_changes": 0,
            "tower_state_changes": 0,
            "building_change_without_tower_change": 0,
            "valid_tower_deltas": 0,
            "invalid_structure_deltas": 0,
            "tower_count_increases": 0,
            "first_game_time_sec": _to_int(ordered[0].get("game_time_sec")) if ordered else None,
            "last_game_time_sec": _to_int(ordered[-1].get("game_time_sec")) if ordered else None,
        }
        prev_row: dict[str, str] | None = None
        prev_state = None
        for row in ordered:
            cur_state = decode_structure_state(_structure_snapshot(row))
            if prev_row is not None and prev_state is not None:
                building_changed = row.get("building_state") != prev_row.get("building_state")
                tower_changed = row.get("tower_state") != prev_row.get("tower_state")
                if building_changed:
                    totals["building_state_changes"] += 1
                    match_summary["building_state_changes"] += 1
                if tower_changed:
                    totals["tower_state_changes"] += 1
                    match_summary["tower_state_changes"] += 1
                if building_changed and not tower_changed:
                    totals["building_change_without_tower_change"] += 1
                    match_summary["building_change_without_tower_change"] += 1
                    if len(examples) < 10:
                        examples.append({
                            "match_id": match_id,
                            "game_time_sec": _to_int(row.get("game_time_sec")),
                            "prev_building_state": prev_row.get("building_state"),
                            "cur_building_state": row.get("building_state"),
                            "tower_state": row.get("tower_state"),
                        })
                delta = diff_structure_state(prev_state, cur_state)
                if delta.valid:
                    totals["valid_tower_deltas"] += 1
                    match_summary["valid_tower_deltas"] += 1
                else:
                    totals["invalid_structure_deltas"] += 1
                    match_summary["invalid_structure_deltas"] += 1
                    if delta.reason == "structure_count_increased":
                        totals["tower_count_increases"] += 1
                        match_summary["tower_count_increases"] += 1
            prev_row = row
            prev_state = cur_state
        match_summaries[match_id] = match_summary

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "totals": totals,
        "building_change_without_tower_examples": examples,
        "matches": match_summaries,
        "interpretation": {
            "tradeable_now": "decoded lane-tower transitions only",
            "research_only": "raw TopLive building_state rax/base/T4 interpretation",
            "survival_rule": "Do not trade rax/base pressure from raw TopLive building_state.",
        },
    }


def load_rows(path: Path, tail_rows: int) -> list[dict[str, str]]:
    lines = _tail_lines(path, tail_rows)
    if len(lines) <= 1:
        return []
    return list(csv.DictReader(lines))


def render_markdown(report: dict[str, Any], path: Path, tail_rows: int) -> str:
    totals = report["totals"]
    lines = [
        "# GetTopLive Structure State Audit",
        "",
        f"Generated: {report['generated_at']}",
        f"Input: `{path}` tail_rows={tail_rows}",
        "",
        "## Totals",
        "",
        f"- top_live_rows: {totals['top_live_rows']}",
        f"- top_live_matches: {totals['top_live_matches']}",
        f"- building_state_changes: {totals['building_state_changes']}",
        f"- tower_state_changes: {totals['tower_state_changes']}",
        f"- building_change_without_tower_change: {totals['building_change_without_tower_change']}",
        f"- valid_tower_deltas: {totals['valid_tower_deltas']}",
        f"- tower_count_increases: {totals['tower_count_increases']}",
        "",
        "## Interpretation",
        "",
        f"- Tradeable now: {report['interpretation']['tradeable_now']}",
        f"- Research only: {report['interpretation']['research_only']}",
        f"- Survival rule: {report['interpretation']['survival_rule']}",
    ]
    examples = report.get("building_change_without_tower_examples", [])
    if examples:
        lines += ["", "## Examples", ""]
        for item in examples:
            lines.append(
                "- match_id={match_id} game_time={game_time_sec} "
                "building {prev_building_state}->{cur_building_state} tower={tower_state}".format(**item)
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, default=RAW_PATH)
    parser.add_argument("--tail-rows", type=int, default=20000)
    parser.add_argument("--json-out", type=Path, default=REPORT_JSON)
    parser.add_argument("--md-out", type=Path, default=REPORT_MD)
    args = parser.parse_args()

    rows = load_rows(args.raw, args.tail_rows)
    report = summarize_rows(rows)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.md_out.write_text(render_markdown(report, args.raw, args.tail_rows), encoding="utf-8")
    print(f"wrote {args.json_out}")
    print(f"wrote {args.md_out}")
    print(json.dumps(report["totals"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
