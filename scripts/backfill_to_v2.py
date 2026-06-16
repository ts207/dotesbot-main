#!/usr/bin/env python3
"""Backfill data_v2/ from existing CSV + JSONL sources.

Sources (priority order — first hit wins on dedup):
  1. logs/<table>.csv                   (live, most recent)
  2. cold_storage/baseline_<date>/*.bak (historical, pre-rotation)
  3. logs/liveleague_raw.jsonl          (raw stream, May 15-16 only)

Targets:
  snapshots       <- raw_snapshots*.csv + rich_context*.csv + liveleague_raw.jsonl
  book_ticks      <- book_events.csv
  dota_events     <- dota_events.csv
  signals         <- signals*.csv
  trade_attempts  <- live_attempts*.csv (trader_kind=live)
                  + paper_trades.csv     (trader_kind=paper)
                  + shadow_trades.csv    (trader_kind=shadow)
                  + scalp_trades.csv     (trader_kind=scalp)
  exits           <- live_exits*.csv
  markouts        <- markouts.csv + signal_markouts.csv (one row per horizon)

Idempotent: re-running drops the destination dirs first.

Usage:
    python3 scripts/backfill_to_v2.py [--table NAME] [--no-clean] [--limit-rows N]
"""
from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from unified_storage.bulk import rows_to_table, write_partitioned
from unified_storage.paths import DATA_V2_ROOT

LOGS = REPO_ROOT / "logs"
COLD = REPO_ROOT / "cold_storage" / "baseline_2026_05_28"

# UUID namespace so the same (match_id, event_type, ts) always produces the
# same signal_id across reruns. Stable across machines.
_NAMESPACE = uuid.UUID("11111111-2222-3333-4444-555555555555")


def _signal_id(match_id: str, event_type: str, received_at_ns: int) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"signal|{match_id}|{event_type}|{received_at_ns}"))


def _attempt_id(match_id: str, token_id: str, received_at_ns: int, trader_kind: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"attempt|{trader_kind}|{match_id}|{token_id}|{received_at_ns}"))


def _ns_from_iso(s: str | None) -> int | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s))
    except ValueError:
        return None
    return int(dt.timestamp() * 1e9)


def _read_csv(path: Path):
    """Yield dict rows from a CSV file. Returns nothing if the file is missing."""
    if not path.exists():
        return
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            yield row


# -----------------------------------------------------------------------------
# Per-table backfill functions
# -----------------------------------------------------------------------------

def backfill_snapshots(limit_rows: int | None = None) -> dict:
    """Snapshots come from three sources. Dedup on (match_id, received_at_ns)."""
    # 2026-05-28 — priority order matters: raw_snapshots.csv has the
    # `data_source` column (top_live vs live_league) which the signal gate
    # depends on. rich_context.csv lacks it, so reading rich_context FIRST
    # would discard the data_source tag on overlapping rows. We therefore
    # read raw_snapshots first; rich_context fills only the rows
    # raw_snapshots doesn't cover (the May 17–21 gap and rich-only games).
    sources = [
        ("raw_snapshots.csv (live)",         LOGS / "raw_snapshots.csv"),
        ("raw_snapshots.csv (bak .051408)",  COLD / "raw_snapshots.csv.20260526_051408.bak"),
        # The .073759 file is byte-identical to .051408 per MANIFEST — skip.
        ("raw_snapshots.csv (bak .083652)",  COLD / "raw_snapshots.csv.20260526_083652.bak"),
        ("rich_context.csv (live)",          LOGS / "rich_context.csv"),
        ("rich_context.csv (bak)",           COLD / "rich_context.csv.20260516_233744.bak"),
    ]
    seen: set[tuple[str, int]] = set()
    rows_out: list[dict] = []
    stats: dict[str, int] = {}
    for label, path in sources:
        if not path.exists():
            stats[label] = 0
            continue
        kept = 0
        for raw in _read_csv(path):
            # Normalize the timestamp column — rich_context uses
            # `timestamp_utc`, raw_snapshots uses `received_at_utc`.
            ts_utc = raw.get("received_at_utc") or raw.get("timestamp_utc")
            ts_ns_raw = raw.get("received_at_ns")
            try:
                ts_ns = int(float(ts_ns_raw)) if ts_ns_raw else (_ns_from_iso(ts_utc) or 0)
            except (TypeError, ValueError):
                ts_ns = _ns_from_iso(ts_utc) or 0
            match_id = raw.get("match_id") or ""
            if not match_id or not ts_ns:
                continue
            key = (match_id, ts_ns)
            if key in seen:
                continue
            seen.add(key)
            # Project into the snapshots schema. Unknown columns are ignored
            # by rows_to_table; missing columns become None.
            rows_out.append({
                "received_at_utc": ts_utc,
                "received_at_ns": ts_ns,
                "match_id": match_id,
                "lobby_id": raw.get("lobby_id"),
                "league_id": raw.get("league_id"),
                "server_steam_id": raw.get("server_steam_id"),
                "game_time_sec": raw.get("game_time_sec"),
                "radiant_lead": raw.get("radiant_lead") or raw.get("net_worth_diff"),
                "radiant_score": raw.get("radiant_score"),
                "dire_score": raw.get("dire_score"),
                "building_state": raw.get("building_state"),
                "tower_state": raw.get("tower_state") or raw.get("radiant_tower_state"),
                "roshan_respawn_timer": raw.get("roshan_respawn_timer"),
                "stream_delay_s": raw.get("stream_delay_s"),
                "source_update_age_sec": raw.get("source_update_age_sec") or raw.get("realtime_stats_age_sec"),
                "data_source": raw.get("data_source"),
                "spectators": raw.get("spectators"),
                "game_over": raw.get("game_over"),
                "series_id": raw.get("series_id"),
                "series_type": raw.get("series_type"),
                "radiant_team": raw.get("radiant_team") or raw.get("radiant_team_name"),
                "dire_team": raw.get("dire_team") or raw.get("dire_team_name"),
                "radiant_team_id": raw.get("radiant_team_id"),
                "dire_team_id": raw.get("dire_team_id"),
                "radiant_net_worth": raw.get("radiant_net_worth"),
                "dire_net_worth": raw.get("dire_net_worth"),
                "net_worth_diff": raw.get("net_worth_diff") or raw.get("radiant_lead"),
            })
            kept += 1
            if limit_rows and len(rows_out) >= limit_rows:
                break
        stats[label] = kept
        if limit_rows and len(rows_out) >= limit_rows:
            break
    # Write in chunks to avoid loading the entire output table at once.
    written = _write_chunks(rows_out, "snapshots", source_file="backfill")
    stats["__written__"] = written
    return stats


def backfill_book_ticks(limit_rows: int | None = None) -> dict:
    rows_out: list[dict] = []
    seen: set[tuple[str, int]] = set()
    src = LOGS / "book_events.csv"
    for raw in _read_csv(src):
        ts_ns = _ns_from_iso(raw.get("timestamp_utc"))
        asset_id = raw.get("asset_id") or ""
        if not asset_id or not ts_ns:
            continue
        key = (asset_id, ts_ns)
        if key in seen:
            continue
        seen.add(key)
        rows_out.append({
            "received_at_utc": raw.get("timestamp_utc"),
            "received_at_ns": ts_ns,
            "asset_id": asset_id,
            "event_type": raw.get("event_type"),
            "source_event_type": raw.get("source_event_type"),
            "best_bid": raw.get("best_bid"),
            "best_ask": raw.get("best_ask"),
            "bid_size": raw.get("bid_size"),
            "ask_size": raw.get("ask_size"),
            "mid": raw.get("mid"),
            "spread": raw.get("spread"),
        })
        if limit_rows and len(rows_out) >= limit_rows:
            break
    written = _write_chunks(rows_out, "book_ticks", source_file="backfill")
    return {"book_events.csv": len(rows_out), "__written__": written}


def backfill_dota_events(limit_rows: int | None = None) -> dict:
    rows_out: list[dict] = []
    seen: set[tuple[str, str, int]] = set()
    src = LOGS / "dota_events.csv"
    for raw in _read_csv(src):
        ts_ns = _ns_from_iso(raw.get("timestamp_utc"))
        match_id = raw.get("match_id") or ""
        event_type = raw.get("event_type") or ""
        if not match_id or not event_type or not ts_ns:
            continue
        key = (match_id, event_type, ts_ns)
        if key in seen:
            continue
        seen.add(key)
        out = dict(raw)
        out["received_at_utc"] = raw.get("timestamp_utc")
        out["received_at_ns"] = ts_ns
        rows_out.append(out)
        if limit_rows and len(rows_out) >= limit_rows:
            break
    written = _write_chunks(rows_out, "dota_events", source_file="backfill")
    return {"dota_events.csv": len(rows_out), "__written__": written}


def backfill_signals(limit_rows: int | None = None) -> dict:
    """signals also get a deterministic signal_id stamped on for downstream joins."""
    sources = [
        ("signals.csv (live)",       LOGS / "signals.csv"),
        ("signals.csv (bak)",        COLD / "signals.csv.20260516_225605.bak"),
    ]
    seen: set[str] = set()
    rows_out: list[dict] = []
    stats: dict[str, int] = {}
    for label, path in sources:
        if not path.exists():
            stats[label] = 0
            continue
        kept = 0
        for raw in _read_csv(path):
            ts_ns = _ns_from_iso(raw.get("timestamp_utc"))
            match_id = raw.get("match_id") or ""
            event_type = raw.get("event_type") or ""
            if not match_id or not ts_ns:
                continue
            sig_id = _signal_id(match_id, event_type, ts_ns)
            if sig_id in seen:
                continue
            seen.add(sig_id)
            out = dict(raw)
            out["signal_id"] = sig_id
            out["received_at_utc"] = raw.get("timestamp_utc")
            out["received_at_ns"] = ts_ns
            rows_out.append(out)
            kept += 1
            if limit_rows and len(rows_out) >= limit_rows:
                break
        stats[label] = kept
        if limit_rows and len(rows_out) >= limit_rows:
            break
    written = _write_chunks(rows_out, "signals", source_file="backfill")
    stats["__written__"] = written
    return stats


def backfill_trade_attempts(limit_rows: int | None = None) -> dict:
    """Union of live_attempts, paper_trades, shadow_trades, scalp_trades.
    Each gets a trader_kind tag and a deterministic attempt_id."""
    sources = [
        ("live",   LOGS / "live_attempts.csv",                              {}),
        ("live",   COLD / "live_attempts.csv.20260516_225605.bak",          {}),
        ("live",   COLD / "live_attempts.csv.20260526_051408.bak",          {}),
        ("paper",  LOGS / "paper_attempts.csv",                             {"col_map": "paper"}),  # bot writes here in paper mode
        ("paper",  LOGS / "paper_trades.csv",                               {"col_map": "paper"}),  # legacy
        ("shadow", LOGS / "shadow_trades.csv",                              {"col_map": "shadow"}),
        ("scalp",  LOGS / "scalp_trades.csv",                               {"col_map": "scalp"}),
    ]
    seen: set[str] = set()
    rows_out: list[dict] = []
    stats: dict[str, int] = {}
    for trader_kind, path, opts in sources:
        if not path.exists():
            stats[f"{trader_kind}:{path.name}"] = 0
            continue
        kept = 0
        for raw in _read_csv(path):
            ts_ns = _ns_from_iso(raw.get("timestamp_utc"))
            if not ts_ns:
                continue
            # Different files have different column names for the token.
            token_id = raw.get("token_id") or raw.get("ride_token") or ""
            match_id = raw.get("match_id") or raw.get("market_id") or ""
            att_id = _attempt_id(match_id, token_id, ts_ns, trader_kind)
            if att_id in seen:
                continue
            seen.add(att_id)
            out = dict(raw)
            out["attempt_id"] = att_id
            out["trader_kind"] = trader_kind
            out["signal_id"] = None  # filled in later by a join script
            out["received_at_utc"] = raw.get("timestamp_utc")
            out["received_at_ns"] = ts_ns
            out["token_id"] = token_id
            out["match_id"] = match_id
            rows_out.append(out)
            kept += 1
            if limit_rows and len(rows_out) >= limit_rows:
                break
        stats[f"{trader_kind}:{path.name}"] = kept
        if limit_rows and len(rows_out) >= limit_rows:
            break
    written = _write_chunks(rows_out, "trade_attempts", source_file="backfill")
    stats["__written__"] = written
    return stats


def backfill_exits(limit_rows: int | None = None) -> dict:
    sources = [
        LOGS / "live_exits.csv",
        LOGS / "paper_exits.csv",  # bot writes here in paper mode
        COLD / "live_exits.csv.20260517_045242.bak",
    ]
    rows_out: list[dict] = []
    seen: set[tuple[str, int]] = set()
    stats: dict[str, int] = {}
    for path in sources:
        if not path.exists():
            stats[path.name] = 0
            continue
        kept = 0
        for raw in _read_csv(path):
            ts_ns = _ns_from_iso(raw.get("timestamp_utc"))
            position_id = raw.get("position_id") or ""
            if not ts_ns or not position_id:
                continue
            key = (position_id, ts_ns)
            if key in seen:
                continue
            seen.add(key)
            out = dict(raw)
            out["received_at_utc"] = raw.get("timestamp_utc")
            out["received_at_ns"] = ts_ns
            rows_out.append(out)
            kept += 1
            if limit_rows and len(rows_out) >= limit_rows:
                break
        stats[path.name] = kept
    written = _write_chunks(rows_out, "exits", source_file="backfill")
    stats["__written__"] = written
    return stats


def backfill_markouts(limit_rows: int | None = None) -> dict:
    """Markouts come from two CSVs and have multiple horizons per row.
    Expand each row into one row per (signal, horizon)."""
    rows_out: list[dict] = []
    seen: set[tuple[str, int]] = set()
    stats: dict[str, int] = {}

    # signal_markouts: rich — has event_type, decision, executable_edge, ref prices
    sm_src = LOGS / "signal_markouts.csv"
    kept = 0
    for raw in _read_csv(sm_src):
        sig_ts_ns = _ns_from_iso(raw.get("signal_timestamp_utc") or raw.get("timestamp_utc"))
        match_id = raw.get("match_id") or ""
        event_type = raw.get("event_type") or ""
        if not sig_ts_ns or not match_id:
            continue
        sig_id = _signal_id(match_id, event_type, sig_ts_ns)
        for horizon in (3, 10, 30):
            key = (sig_id, horizon)
            if key in seen:
                continue
            seen.add(key)
            rows_out.append({
                "signal_id": sig_id,
                "signal_received_at_ns": sig_ts_ns,
                "computed_at_ns": _ns_from_iso(raw.get("timestamp_utc")),
                "match_id": match_id,
                "market_name": raw.get("market_name"),
                "token_id": raw.get("token_id"),
                "event_type": event_type,
                "horizon_sec": horizon,
                "reference_price": raw.get("reference_price"),
                "reference_bid": raw.get("reference_bid"),
                "reference_ask": raw.get("reference_ask"),
                "markout_price_delta": raw.get(f"markout_{horizon}s"),
                "edge_after": raw.get(f"edge_after_{horizon}s"),
                "decision_at_signal": raw.get("decision"),
                "side": raw.get("side"),
                "received_at_ns": sig_ts_ns,   # for date-partition derivation
            })
            kept += 1
            if limit_rows and len(rows_out) >= limit_rows:
                break
    stats["signal_markouts.csv"] = kept

    # markouts.csv: lean schema, same expansion
    m_src = LOGS / "markouts.csv"
    kept = 0
    for raw in _read_csv(m_src):
        ts_ns = _ns_from_iso(raw.get("timestamp_utc"))
        match_id = raw.get("match_id") or ""
        event_type = raw.get("event_type") or ""
        if not ts_ns or not match_id:
            continue
        sig_id = _signal_id(match_id, event_type, ts_ns)
        for horizon in (3, 10, 30):
            key = (sig_id, horizon)
            if key in seen:
                continue
            seen.add(key)
            rows_out.append({
                "signal_id": sig_id,
                "signal_received_at_ns": ts_ns,
                "computed_at_ns": ts_ns,
                "match_id": match_id,
                "market_name": raw.get("market_name"),
                "token_id": raw.get("token_id"),
                "event_type": event_type,
                "horizon_sec": horizon,
                "reference_price": raw.get("reference_price"),
                "markout_price_delta": raw.get(f"markout_{horizon}s"),
                "received_at_ns": ts_ns,
            })
            kept += 1
    stats["markouts.csv"] = kept

    written = _write_chunks(rows_out, "markouts", source_file="backfill")
    stats["__written__"] = written
    return stats


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _write_chunks(rows: list[dict], table_name: str, source_file: str, chunk_size: int = 50_000) -> dict[str, int]:
    """Write rows in chunks so we don't materialize 100K+ rows in arrow at once."""
    written: dict[str, int] = {}
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i : i + chunk_size]
        if not chunk:
            continue
        t = rows_to_table(chunk, table_name, source_file=source_file)
        for d, n in write_partitioned(t, table_name).items():
            written[d] = written.get(d, 0) + n
    return written


def _clean_table_dir(table_name: str) -> None:
    p = DATA_V2_ROOT / table_name
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True, exist_ok=True)


TABLES = {
    "snapshots":      backfill_snapshots,
    "book_ticks":     backfill_book_ticks,
    "dota_events":    backfill_dota_events,
    "signals":        backfill_signals,
    "trade_attempts": backfill_trade_attempts,
    "exits":          backfill_exits,
    "markouts":       backfill_markouts,
}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--table", help="only this table (default: all)")
    ap.add_argument("--no-clean", action="store_true", help="don't wipe destination dirs first")
    ap.add_argument("--limit-rows", type=int, help="cap rows per table (for quick smoke tests)")
    args = ap.parse_args()

    targets = [args.table] if args.table else list(TABLES.keys())
    for t in targets:
        if t not in TABLES:
            print(f"unknown table: {t}", file=sys.stderr)
            sys.exit(2)

    for table in targets:
        if not args.no_clean:
            _clean_table_dir(table)
        print(f"\n=== {table} ===")
        stats = TABLES[table](limit_rows=args.limit_rows)
        for k, v in stats.items():
            if k == "__written__":
                if v:
                    by_date = ", ".join(f"{d}={n}" for d, n in sorted(v.items()))
                    print(f"  written → {by_date}")
                else:
                    print(f"  written → 0 partitions")
            else:
                print(f"  {k}: {v} rows kept")


if __name__ == "__main__":
    main()
