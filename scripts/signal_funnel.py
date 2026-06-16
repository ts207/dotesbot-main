"""Aggregate live_attempts.csv reject reasons into a per-event funnel.

Reject reasons are enriched with their triggering values (e.g.
'event_quality_too_low:q=0.450_min=0.600') so we can see how close near-misses
are. This script groups by the reason prefix (text before ':') and shows
counts per event_type.

Usage:
    python3 scripts/signal_funnel.py
    python3 scripts/signal_funnel.py --since 2026-05-25T00:00:00+00:00
    python3 scripts/signal_funnel.py --event POLL_FIGHT_SWING --verbose
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


def _parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _reason_prefix(reason: str) -> str:
    return (reason or "").split(":", 1)[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-attempts", default="logs/live_attempts.csv")
    parser.add_argument("--since", default="2026-05-19T00:00:00+00:00",
                        help="ISO timestamp; only rows at or after this are counted.")
    parser.add_argument("--event", default=None,
                        help="Optional event_type filter. Without it, all events are summarized.")
    parser.add_argument("--verbose", action="store_true",
                        help="Show the full reject reason (with values) for each row, not just the prefix.")
    args = parser.parse_args()

    since = _parse_ts(args.since) or datetime(2026, 5, 19, tzinfo=timezone.utc)
    path = Path(args.live_attempts)
    if not path.exists():
        print(f"missing {path}")
        return

    by_event_status: dict[str, Counter] = defaultdict(Counter)
    by_event_reason: dict[str, Counter] = defaultdict(Counter)
    by_event_total: Counter = Counter()
    verbose_examples: dict[tuple[str, str], list[str]] = defaultdict(list)

    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("phase") or "submit") != "submit":
                continue
            ts = _parse_ts(row.get("timestamp_utc", ""))
            if ts is None or ts < since:
                continue
            ev = row.get("event_type") or "UNKNOWN"
            if args.event and ev != args.event:
                continue
            by_event_total[ev] += 1
            status = row.get("order_status") or ""
            by_event_status[ev][status] += 1
            reason = row.get("reason_if_rejected") or ""
            if reason:
                prefix = _reason_prefix(reason)
                by_event_reason[ev][prefix] += 1
                if args.verbose and len(verbose_examples[(ev, prefix)]) < 3:
                    verbose_examples[(ev, prefix)].append(reason)

    if not by_event_total:
        print("no submit rows in window")
        return

    print(f"signal funnel since {since.isoformat()}  (event filter: {args.event or 'ALL'})")
    print()
    for ev in sorted(by_event_total, key=lambda e: -by_event_total[e]):
        total = by_event_total[ev]
        statuses = by_event_status[ev]
        delayed = statuses.get("delayed", 0)
        matched = statuses.get("matched", 0) + statuses.get("filled", 0)
        rejected = statuses.get("rejected_precheck", 0)
        exception = statuses.get("exception", 0)
        print(f"--- {ev}: {total} signals ({rejected} rejected, {delayed} delayed, {matched} matched, {exception} exception) ---")
        for prefix, count in by_event_reason[ev].most_common():
            pct = 100.0 * count / total
            print(f"    {count:4d} ({pct:5.1f}%)  {prefix}")
            if args.verbose:
                for example in verbose_examples.get((ev, prefix), []):
                    print(f"          e.g. {example}")
        print()


if __name__ == "__main__":
    main()
