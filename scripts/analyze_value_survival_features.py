#!/usr/bin/env python3
"""Join VALUE replay trades to GetTopLive score/structure context.

The goal is to find survival gates that are supported by checked-in replay
evidence. With the current small sample, this script is deliberately conservative:
it reports candidate subgroups and marks them research-only unless support is
large enough to justify a live gate.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from structure_state import decode_structure_state


TRADES_PATH = REPO_ROOT / "reports" / "value_bot_raw_replay_trades.csv"
RAW_PATHS = [
    REPO_ROOT / "logs" / "raw_snapshots.csv",
    REPO_ROOT / "logs" / "raw_snapshots.csv.20260602_225219_578400.gz",
]
REPORT_JSON = REPO_ROOT / "reports" / "value_survival_feature_audit.json"
REPORT_MD = REPO_ROOT / "reports" / "value_survival_feature_audit.md"


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", newline="", encoding="utf-8", errors="replace")
    return path.open(newline="", encoding="utf-8", errors="replace")


def load_value_trades(path: Path) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("variant") != "no_confirmation":
                continue
            if str(row.get("causality_valid")).lower() != "true":
                continue
            if str(row.get("manual_excluded")).lower() == "true":
                continue
            row["_decision_ns"] = _to_int(row.get("latest_snapshot_ts_used") or row.get("decision_ts"))
            row["_lead"] = _to_int(row.get("lead"))
            row["_pnl_usd"] = _to_float(row.get("pnl_usd")) or 0.0
            row["_won"] = 1 if str(row.get("won")) == "1" else 0
            trades.append(row)
    return trades


def iter_target_snapshots(
    paths: Iterable[Path],
    target_ids: set[str],
    max_decision_ns: int | None = None,
) -> Iterable[dict[str, str]]:
    for path in paths:
        if not path.exists():
            continue
        seen_ids: set[str] = set()
        with _open_text(path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                ns = _to_int(row.get("received_at_ns"))
                if max_decision_ns is not None and ns is not None and ns > max_decision_ns and seen_ids >= target_ids:
                    break
                if row.get("match_id") in target_ids and row.get("data_source") == "top_live":
                    seen_ids.add(str(row.get("match_id")))
                    yield row


def load_snapshots_by_match(
    paths: list[Path],
    target_ids: set[str],
    max_decision_ns: int | None = None,
) -> dict[str, list[dict[str, str]]]:
    by_match: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in iter_target_snapshots(paths, target_ids, max_decision_ns=max_decision_ns):
        by_match[str(row.get("match_id"))].append(row)
    for rows in by_match.values():
        rows.sort(key=lambda row: _to_int(row.get("received_at_ns")) or 0)
    return by_match


def nearest_snapshot(rows: list[dict[str, str]], decision_ns: int | None) -> dict[str, str] | None:
    if not rows or decision_ns is None:
        return None
    best = None
    for row in rows:
        ns = _to_int(row.get("received_at_ns")) or 0
        if ns <= decision_ns:
            best = row
        else:
            break
    return best


def _tower_counts(row: dict[str, str]) -> tuple[int | None, int | None]:
    state = decode_structure_state({
        "match_id": row.get("match_id"),
        "game_time_sec": row.get("game_time_sec"),
        "building_state": row.get("building_state"),
        "building_state_schema": "top_live_lane_tower_progress",
        "tower_state": row.get("tower_state"),
    })
    rad = [
        state.radiant_t1_alive,
        state.radiant_t2_alive,
        state.radiant_t3_alive,
        state.radiant_t4_alive,
    ]
    dire = [
        state.dire_t1_alive,
        state.dire_t2_alive,
        state.dire_t3_alive,
        state.dire_t4_alive,
    ]
    if any(x is None for x in rad + dire):
        return None, None
    return int(sum(rad)), int(sum(dire))


def enrich_trade(trade: dict[str, Any], snapshots: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
    row = nearest_snapshot(snapshots.get(str(trade.get("match_id")), []), trade.get("_decision_ns"))
    out = dict(trade)
    if row is None:
        out["snapshot_joined"] = False
        return out
    lead = trade.get("_lead")
    leader_sign = 1 if (lead or 0) > 0 else -1
    rad_score = _to_int(row.get("radiant_score"))
    dire_score = _to_int(row.get("dire_score"))
    rad_towers, dire_towers = _tower_counts(row)
    snapshot_ns = _to_int(row.get("received_at_ns"))
    out.update({
        "snapshot_joined": True,
        "snapshot_received_at_ns": snapshot_ns,
        "snapshot_age_ms": round(((trade.get("_decision_ns") or 0) - (snapshot_ns or 0)) / 1_000_000, 3) if snapshot_ns else None,
        "snapshot_game_time_sec": _to_int(row.get("game_time_sec")),
        "snapshot_radiant_lead": _to_int(row.get("radiant_lead")),
        "snapshot_radiant_score": rad_score,
        "snapshot_dire_score": dire_score,
        "snapshot_building_state": row.get("building_state"),
        "snapshot_tower_state": row.get("tower_state"),
        "leader_side": "radiant" if leader_sign > 0 else "dire",
        "leader_kill_diff": leader_sign * ((rad_score or 0) - (dire_score or 0)) if rad_score is not None and dire_score is not None else None,
        "radiant_towers_alive": rad_towers,
        "dire_towers_alive": dire_towers,
        "leader_tower_diff": leader_sign * ((rad_towers or 0) - (dire_towers or 0)) if rad_towers is not None and dire_towers is not None else None,
        "leader_enemy_towers_down": (11 - (dire_towers or 0)) if leader_sign > 0 and dire_towers is not None else ((11 - (rad_towers or 0)) if rad_towers is not None else None),
        "leader_own_towers_down": (11 - (rad_towers or 0)) if leader_sign > 0 and rad_towers is not None else ((11 - (dire_towers or 0)) if dire_towers is not None else None),
    })
    return out


def summarize_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    pnl = sum(float(row.get("_pnl_usd") or 0.0) for row in rows)
    stake = sum(_to_float(row.get("stake_usd")) or 0.0 for row in rows)
    wins = sum(int(row.get("_won") or 0) for row in rows)
    return {
        "trades": n,
        "wins": wins,
        "losses": n - wins,
        "win_pct": round(wins / n * 100.0, 1) if n else None,
        "pnl_usd": round(pnl, 2),
        "roi_pct": round(pnl / stake * 100.0, 1) if stake else None,
    }


def bucketize(enriched: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in enriched:
        if not row.get("snapshot_joined"):
            buckets["join:missing"].append(row)
            continue
        kill = row.get("leader_kill_diff")
        tower = row.get("leader_tower_diff")
        enemy_down = row.get("leader_enemy_towers_down")
        own_down = row.get("leader_own_towers_down")
        buckets[f"leader_kills:{'ahead_or_tied' if kill is not None and kill >= 0 else 'behind'}"].append(row)
        buckets[f"leader_towers:{'ahead_or_tied' if tower is not None and tower >= 0 else 'behind'}"].append(row)
        buckets[f"enemy_towers_down:{'>=3' if enemy_down is not None and enemy_down >= 3 else '<3'}"].append(row)
        buckets[f"own_towers_down:{'>=3' if own_down is not None and own_down >= 3 else '<3'}"].append(row)
        if kill is not None and tower is not None:
            aligned = kill >= 0 and tower >= 0
            buckets[f"score_and_tower:{'aligned' if aligned else 'not_aligned'}"].append(row)
    return {name: summarize_group(rows) for name, rows in sorted(buckets.items())}


def gate_recommendation(summary: dict[str, dict[str, Any]], baseline: dict[str, Any]) -> dict[str, Any]:
    candidates = []
    for name, stats in summary.items():
        n = stats.get("trades") or 0
        roi = stats.get("roi_pct")
        win = stats.get("win_pct")
        if n >= 10 and roi is not None and win is not None and roi > (baseline.get("roi_pct") or 0) and win >= (baseline.get("win_pct") or 0):
            candidates.append({"bucket": name, **stats})
    return {
        "live_gate_change": "none",
        "reason": "Sample is small and no score/structure subgroup is promoted automatically. Use as research evidence only unless a fresh replay expands support.",
        "candidate_observations": candidates,
    }


def build_report(trades_path: Path, raw_paths: list[Path]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    trades = load_value_trades(trades_path)
    max_decision_ns = max((int(t["_decision_ns"]) for t in trades if t.get("_decision_ns")), default=None)
    snapshots = load_snapshots_by_match(
        raw_paths,
        {str(t.get("match_id")) for t in trades},
        max_decision_ns=max_decision_ns,
    )
    enriched = [enrich_trade(trade, snapshots) for trade in trades]
    joined = [row for row in enriched if row.get("snapshot_joined")]
    baseline = summarize_group(enriched)
    joined_summary = summarize_group(joined)
    bucket_summary = bucketize(enriched)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "trades_path": str(trades_path),
        "raw_paths": [str(path) for path in raw_paths if path.exists()],
        "baseline": baseline,
        "joined_summary": joined_summary,
        "join_coverage_pct": round(len(joined) / len(enriched) * 100.0, 1) if enriched else 0.0,
        "bucket_summary": bucket_summary,
        "recommendation": gate_recommendation(bucket_summary, baseline),
    }
    return report, enriched


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# VALUE Survival Feature Audit",
        "",
        f"Generated: {report['generated_at']}",
        f"Join coverage: {report['join_coverage_pct']}%",
        "",
        "## Baseline",
        "",
        "- trades={trades} wins={wins} losses={losses} win_pct={win_pct}% roi={roi_pct}% pnl=${pnl_usd}".format(**report["baseline"]),
        "",
        "## Joined TopLive Context",
        "",
        "- trades={trades} wins={wins} losses={losses} win_pct={win_pct}% roi={roi_pct}% pnl=${pnl_usd}".format(**report["joined_summary"]),
        "",
        "## Buckets",
        "",
    ]
    for name, stats in report["bucket_summary"].items():
        lines.append("- {name}: trades={trades} win_pct={win_pct}% roi={roi_pct}% pnl=${pnl_usd}".format(name=name, **stats))
    rec = report["recommendation"]
    lines += [
        "",
        "## Recommendation",
        "",
        f"- Live gate change: {rec['live_gate_change']}",
        f"- Reason: {rec['reason']}",
    ]
    return "\n".join(lines) + "\n"


def write_enriched_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    keys = [
        "match_id", "decision_utc", "won", "pnl_usd", "entry_price", "fair", "edge", "lead", "game_time_sec",
        "snapshot_joined", "snapshot_age_ms", "leader_side", "leader_kill_diff", "leader_tower_diff",
        "leader_enemy_towers_down", "leader_own_towers_down", "snapshot_building_state", "snapshot_tower_state",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in keys})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trades", type=Path, default=TRADES_PATH)
    parser.add_argument("--raw", type=Path, action="append", default=None)
    parser.add_argument("--json-out", type=Path, default=REPORT_JSON)
    parser.add_argument("--md-out", type=Path, default=REPORT_MD)
    parser.add_argument("--csv-out", type=Path, default=REPO_ROOT / "reports" / "value_survival_feature_trades.csv")
    args = parser.parse_args()

    raw_paths = args.raw if args.raw else RAW_PATHS
    report, enriched = build_report(args.trades, raw_paths)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.md_out.write_text(render_markdown(report), encoding="utf-8")
    write_enriched_csv(args.csv_out, enriched)
    print(f"wrote {args.json_out}")
    print(f"wrote {args.md_out}")
    print(f"wrote {args.csv_out}")
    print(json.dumps({"baseline": report["baseline"], "join_coverage_pct": report["join_coverage_pct"], "recommendation": report["recommendation"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
