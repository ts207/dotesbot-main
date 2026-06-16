from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from signal_engine import _EVENT_MAX_FILL


def _parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _f(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _read_csv(path: Path, since: datetime) -> list[dict[str, str]]:
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            ts = _parse_ts(row.get("timestamp_utc", ""))
            if ts and ts >= since:
                out.append(row)
    return out


def _summarize_markouts(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_event[row.get("event_type") or "UNKNOWN"].append(row)

    summary = []
    for event_type, event_rows in sorted(by_event.items()):
        m30 = [_f(r.get("markout_30s")) for r in event_rows]
        m30 = [x for x in m30 if x is not None]
        m10 = [_f(r.get("markout_10s")) for r in event_rows]
        m10 = [x for x in m10 if x is not None]
        decisions = Counter(r.get("decision") or "" for r in event_rows)
        skips = Counter(r.get("skip_reason") or "" for r in event_rows if r.get("skip_reason"))
        summary.append({
            "event_type": event_type,
            "n": len(event_rows),
            "n_m30": len(m30),
            "taken": decisions.get("take", 0) + decisions.get("live", 0),
            "skipped": decisions.get("skip", 0),
            "avg_m10": mean(m10) if m10 else None,
            "avg_m30": mean(m30) if m30 else None,
            "median_m30": median(m30) if m30 else None,
            "win_rate_m30": sum(1 for x in m30 if x > 0) / len(m30) if m30 else None,
            "top_skip": skips.most_common(1)[0][0] if skips else "",
        })
    summary.sort(key=lambda r: (r["avg_m30"] is None, -(r["avg_m30"] or -999), -r["n"]))
    return summary


def _summarize_attempts(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    submit_rows = [r for r in rows if (r.get("phase") or "submit") == "submit"]
    by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in submit_rows:
        by_event[row.get("event_type") or "UNKNOWN"].append(row)

    summary = []
    for event_type, event_rows in sorted(by_event.items()):
        statuses = Counter(r.get("order_status") or "" for r in event_rows)
        submitted = [_f(r.get("submitted_size_usd")) or 0.0 for r in event_rows]
        filled = [_f(r.get("filled_size_usd")) or 0.0 for r in event_rows]
        summary.append({
            "event_type": event_type,
            "attempts": len(event_rows),
            "submitted_usd": sum(submitted),
            "filled_usd": sum(filled),
            "statuses": ", ".join(f"{k}:{v}" for k, v in statuses.most_common()),
        })
    summary.sort(key=lambda r: (-r["submitted_usd"], -r["attempts"], r["event_type"]))
    return summary


def _summarize_cap_buckets(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    buckets = [
        ("<0.65", None, 0.65),
        ("0.65-0.80", 0.65, 0.80),
        ("0.80-0.88", 0.80, 0.88),
        (">=0.88", 0.88, None),
    ]
    out = []
    for event_type in sorted({r.get("event_type") or "UNKNOWN" for r in rows}):
        event_rows = [r for r in rows if (r.get("event_type") or "UNKNOWN") == event_type]
        cap = _EVENT_MAX_FILL.get(event_type)
        for label, lo, hi in buckets:
            bucket_rows = []
            for row in event_rows:
                ask = _f(row.get("reference_ask")) or _f(row.get("reference_price"))
                if ask is None:
                    continue
                if lo is not None and ask < lo:
                    continue
                if hi is not None and ask >= hi:
                    continue
                bucket_rows.append(row)
            m30 = [_f(r.get("markout_30s")) for r in bucket_rows]
            m30 = [x for x in m30 if x is not None]
            if not m30:
                continue
            out.append({
                "event_type": event_type,
                "current_cap": cap,
                "bucket": label,
                "n": len(m30),
                "avg_m30": mean(m30),
                "win_rate_m30": sum(1 for x in m30 if x > 0) / len(m30),
            })
    out.sort(key=lambda r: (r["event_type"], r["bucket"]))
    return out


def _fmt(value: object, digits: int = 4) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-05-19T00:00:00+00:00")
    parser.add_argument("--signal-markouts", default="logs/signal_markouts.csv")
    parser.add_argument("--live-attempts", default="logs/live_attempts.csv")
    parser.add_argument("--out", default="validations/current_event_markouts.md")
    args = parser.parse_args()

    since = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
    signal_rows = _read_csv(Path(args.signal_markouts), since)
    attempt_rows = _read_csv(Path(args.live_attempts), since)
    markout_summary = _summarize_markouts(signal_rows)
    attempt_summary = _summarize_attempts(attempt_rows)
    cap_summary = _summarize_cap_buckets(signal_rows)

    lines = [
        "# Current Event Markouts",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"Since: {since.isoformat()}",
        "",
        f"Signal markout rows: {len(signal_rows)}",
        f"Live attempt rows: {len(attempt_rows)}",
        "",
        "## Signal Markouts By Event",
        "",
        "| event_type | n | n_m30 | avg_m10 | avg_m30 | median_m30 | win_rate_m30 | skipped | top_skip |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in markout_summary:
        lines.append(
            "| {event_type} | {n} | {n_m30} | {avg_m10} | {avg_m30} | {median_m30} | {win_rate_m30} | {skipped} | {top_skip} |".format(
                event_type=row["event_type"],
                n=row["n"],
                n_m30=row["n_m30"],
                avg_m10=_fmt(row["avg_m10"]),
                avg_m30=_fmt(row["avg_m30"]),
                median_m30=_fmt(row["median_m30"]),
                win_rate_m30=_fmt(row["win_rate_m30"], 2),
                skipped=row["skipped"],
                top_skip=row["top_skip"],
            )
        )

    lines.extend([
        "",
        "## Live Attempts By Event",
        "",
        "| event_type | attempts | submitted_usd | filled_usd | statuses |",
        "|---|---:|---:|---:|---|",
    ])
    for row in attempt_summary:
        lines.append(
            "| {event_type} | {attempts} | {submitted_usd} | {filled_usd} | {statuses} |".format(
                event_type=row["event_type"],
                attempts=row["attempts"],
                submitted_usd=_fmt(row["submitted_usd"], 2),
                filled_usd=_fmt(row["filled_usd"], 2),
                statuses=row["statuses"],
            )
        )

    lines.extend([
        "",
        "## Cap Diagnostics By Reference Ask",
        "",
        "| event_type | current_cap | ask_bucket | n | avg_m30 | win_rate_m30 |",
        "|---|---:|---|---:|---:|---:|",
    ])
    for row in cap_summary:
        lines.append(
            "| {event_type} | {current_cap} | {bucket} | {n} | {avg_m30} | {win_rate_m30} |".format(
                event_type=row["event_type"],
                current_cap=_fmt(row["current_cap"], 2),
                bucket=row["bucket"],
                n=row["n"],
                avg_m30=_fmt(row["avg_m30"]),
                win_rate_m30=_fmt(row["win_rate_m30"], 2),
            )
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
