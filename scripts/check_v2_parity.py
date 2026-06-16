#!/usr/bin/env python3
"""Parity check: data_v2/ Parquet vs original logs/ CSV.

For each table, compare:
  - row count
  - sum of one or two numeric columns (catches data corruption)
  - distinct match_ids or asset_ids (catches partial loads)
  - date coverage (catches missing partitions)

The CSV side may have more raw rows than Parquet (we dedupe on PK during
backfill), so the report shows both numbers and explicitly notes when the
gap is expected.

Usage:
    python3 scripts/check_v2_parity.py
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import pyarrow.dataset as pds

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from unified_storage.paths import DATA_V2_ROOT

LOGS = REPO_ROOT / "logs"
COLD = REPO_ROOT / "cold_storage" / "baseline_2026_05_28"


def _ts_to_date(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s)).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _csv_summary(paths, *, ts_col, id_col, sum_cols, dedup_key=None):
    """Walk a list of CSV files, counting rows and aggregating columns.
    Optional `dedup_key` is a (callable rowdict -> key) — when set, only
    the first occurrence of each key counts toward "kept" rows."""
    raw_n = 0
    kept_n = 0
    sums = {c: 0.0 for c in sum_cols}
    ids = set()
    dates = Counter()
    seen = set() if dedup_key else None
    for path in paths:
        if not path.exists():
            continue
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                raw_n += 1
                if seen is not None:
                    k = dedup_key(row)
                    if k in seen or k is None:
                        continue
                    seen.add(k)
                kept_n += 1
                for c in sum_cols:
                    v = row.get(c)
                    if v in (None, ""):
                        continue
                    try:
                        sums[c] += float(v)
                    except ValueError:
                        pass
                if id_col:
                    iv = row.get(id_col)
                    if iv:
                        ids.add(iv)
                d = _ts_to_date(row.get(ts_col))
                if d:
                    dates[d] += 1
    return {"raw_rows": raw_n, "kept_rows": kept_n, "sums": sums, "ids": len(ids), "dates": dict(dates)}


def _pq_summary(table_name, *, id_col, sum_cols):
    p = DATA_V2_ROOT / table_name
    if not p.exists() or not any(p.rglob("*.parquet")):
        return None
    ds = pds.dataset(str(p), format="parquet", partitioning="hive")
    cols = [c for c in {id_col, "date", *sum_cols} if c]
    t = ds.to_table(columns=[c for c in cols if c in ds.schema.names])
    sums = {}
    for c in sum_cols:
        if c in t.column_names:
            vals = [v for v in t.column(c).to_pylist() if v is not None]
            sums[c] = sum(vals)
        else:
            sums[c] = None
    ids = set(t.column(id_col).to_pylist()) if id_col and id_col in t.column_names else set()
    dates = Counter(t.column("date").to_pylist()) if "date" in t.column_names else Counter()
    return {"rows": t.num_rows, "sums": sums, "ids": len(ids), "dates": dict(dates)}


def _fmt_num(x):
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:,.2f}"
    return f"{x:,}"


def _report(table_name, csv_stats, pq_stats):
    print(f"\n=== {table_name} ===")
    if pq_stats is None:
        print("  Parquet partition empty — skipping")
        return False
    print(f"  CSV rows (raw):     {csv_stats['raw_rows']:>10,}")
    print(f"  CSV rows (deduped): {csv_stats['kept_rows']:>10,}")
    print(f"  Parquet rows:       {pq_stats['rows']:>10,}")
    drift = pq_stats["rows"] - csv_stats["kept_rows"]
    status = "OK" if drift == 0 else f"DRIFT {drift:+,}"
    print(f"  Row parity:         {status}")
    for c in csv_stats["sums"]:
        cs = csv_stats["sums"][c]
        ps = pq_stats["sums"].get(c)
        if ps is None:
            continue
        d = abs(ps - cs)
        rel = d / max(abs(cs), 1.0)
        tag = "OK" if rel < 1e-6 else f"DRIFT {ps - cs:+.4f}"
        print(f"  Σ {c:<20} CSV={_fmt_num(cs):>16}  PQ={_fmt_num(ps):>16}  {tag}")
    if csv_stats["ids"]:
        print(f"  Distinct IDs: CSV={csv_stats['ids']:,}  PQ={pq_stats['ids']:,}")
    csv_dates = set(csv_stats["dates"])
    pq_dates = set(pq_stats["dates"])
    missing_in_pq = csv_dates - pq_dates
    extra_in_pq = pq_dates - csv_dates
    if missing_in_pq:
        print(f"  Dates in CSV but missing from Parquet: {sorted(missing_in_pq)}")
    if extra_in_pq:
        print(f"  Dates in Parquet but not in CSV: {sorted(extra_in_pq)}")
    return drift == 0


def check_snapshots():
    # The backfill writes a unified `radiant_lead` column that takes its value
    # from `radiant_lead` when present (raw_snapshots) and falls back to
    # `net_worth_diff` (rich_context, which uses the rich-stats column name).
    # To get an apples-to-apples CSV reference we have to apply the same
    # column-coalescing rule when summing the CSV side.
    csv_paths = [
        LOGS / "raw_snapshots.csv",
        COLD / "raw_snapshots.csv.20260526_051408.bak",
        COLD / "raw_snapshots.csv.20260526_083652.bak",
        LOGS / "rich_context.csv",
        COLD / "rich_context.csv.20260516_233744.bak",
    ]
    seen = set()
    raw_n = kept_n = 0
    sum_lead = sum_rs = sum_ds = 0.0
    ids = set()
    dates = Counter()
    for path in csv_paths:
        if not path.exists():
            continue
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                raw_n += 1
                # Backfill's dedup key
                ts_ns = row.get("received_at_ns")
                ts_utc = row.get("received_at_utc") or row.get("timestamp_utc")
                key = (row.get("match_id"), ts_ns or ts_utc)
                if key in seen or not key[0] or not key[1]:
                    continue
                seen.add(key)
                kept_n += 1
                lead_val = row.get("radiant_lead") or row.get("net_worth_diff")
                if lead_val not in (None, ""):
                    try:
                        sum_lead += float(lead_val)
                    except ValueError:
                        pass
                for v, target in ((row.get("radiant_score"), "rs"),
                                  (row.get("dire_score"),   "ds")):
                    if v in (None, ""):
                        continue
                    try:
                        if target == "rs":
                            sum_rs += float(v)
                        else:
                            sum_ds += float(v)
                    except ValueError:
                        pass
                if row.get("match_id"):
                    ids.add(row["match_id"])
                d = _ts_to_date(ts_utc)
                if d:
                    dates[d] += 1
    csv_stats = {
        "raw_rows": raw_n, "kept_rows": kept_n,
        "sums": {"radiant_lead": sum_lead, "radiant_score": sum_rs, "dire_score": sum_ds},
        "ids": len(ids), "dates": dict(dates),
    }
    pq_stats = _pq_summary("snapshots", id_col="match_id",
                           sum_cols=["radiant_lead", "radiant_score", "dire_score"])
    _report("snapshots", csv_stats, pq_stats)


def check_book_ticks():
    csv_stats = _csv_summary(
        [LOGS / "book_events.csv"],
        ts_col="timestamp_utc",
        id_col="asset_id",
        sum_cols=["best_bid", "best_ask"],
        dedup_key=lambda r: (r.get("asset_id"), r.get("timestamp_utc")),
    )
    pq_stats = _pq_summary("book_ticks", id_col="asset_id",
                           sum_cols=["best_bid", "best_ask"])
    _report("book_ticks", csv_stats, pq_stats)


def check_dota_events():
    csv_stats = _csv_summary(
        [LOGS / "dota_events.csv"],
        ts_col="timestamp_utc",
        id_col="match_id",
        sum_cols=["networth_delta", "kill_diff_delta"],
        dedup_key=lambda r: (r.get("match_id"), r.get("event_type"), r.get("timestamp_utc")),
    )
    pq_stats = _pq_summary("dota_events", id_col="match_id",
                           sum_cols=["networth_delta", "kill_diff_delta"])
    _report("dota_events", csv_stats, pq_stats)


def check_signals():
    csv_stats = _csv_summary(
        [LOGS / "signals.csv",
         COLD / "signals.csv.20260516_225605.bak"],
        ts_col="timestamp_utc",
        id_col="match_id",
        sum_cols=["fair_price", "executable_edge"],
        dedup_key=lambda r: (r.get("match_id"), r.get("event_type"), r.get("timestamp_utc")),
    )
    pq_stats = _pq_summary("signals", id_col="match_id",
                           sum_cols=["fair_price", "executable_edge"])
    _report("signals", csv_stats, pq_stats)


def check_exits():
    csv_stats = _csv_summary(
        [LOGS / "live_exits.csv",
         COLD / "live_exits.csv.20260517_045242.bak"],
        ts_col="timestamp_utc",
        id_col="position_id",
        sum_cols=["shares_filled", "best_bid"],
        dedup_key=lambda r: (r.get("position_id"), r.get("timestamp_utc")),
    )
    pq_stats = _pq_summary("exits", id_col="position_id",
                           sum_cols=["shares_filled", "best_bid"])
    _report("exits", csv_stats, pq_stats)


def check_markouts():
    """Markouts go 1 CSV row → 3 Parquet rows (one per horizon). We can't
    parity-check rowcount directly; instead, count CSV rows and compare to
    Parquet/3."""
    csv_stats = _csv_summary(
        [LOGS / "signal_markouts.csv", LOGS / "markouts.csv"],
        ts_col="timestamp_utc",
        id_col="match_id",
        sum_cols=["markout_30s"],
        dedup_key=lambda r: (r.get("match_id"), r.get("event_type"),
                             r.get("signal_timestamp_utc") or r.get("timestamp_utc")),
    )
    pq_stats = _pq_summary("markouts", id_col="match_id",
                           sum_cols=["markout_price_delta"])
    print(f"\n=== markouts (note: 1 CSV row → 3 Parquet rows, one per horizon) ===")
    print(f"  CSV rows (raw):     {csv_stats['raw_rows']:>10,}")
    print(f"  CSV rows (deduped): {csv_stats['kept_rows']:>10,}")
    if pq_stats:
        print(f"  Parquet rows:       {pq_stats['rows']:>10,}  (expect ≈ 3 × CSV-deduped)")
        ratio = pq_stats["rows"] / max(csv_stats["kept_rows"], 1)
        print(f"  Expansion ratio:    {ratio:.2f}")


def main():
    print(f"Parity check: logs/ vs data_v2/ at {datetime.utcnow().isoformat()}Z")
    check_snapshots()
    check_book_ticks()
    check_dota_events()
    check_signals()
    check_exits()
    check_markouts()


if __name__ == "__main__":
    main()
