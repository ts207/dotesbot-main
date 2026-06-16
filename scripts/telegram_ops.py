from __future__ import annotations

import argparse
import csv
import json
import os
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _f(value: str | None) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _read_recent(path: Path, since: datetime) -> list[dict[str, str]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            ts = _parse_ts(row.get("timestamp_utc", ""))
            if ts and ts >= since:
                rows.append(row)
    return rows


def send_telegram(text: str, *, require_send: bool = False) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print(text)
        if require_send:
            raise SystemExit("missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return False

    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(f"telegram send failed: {payload}")
    return True


def _last_csv_timestamp(path: Path) -> datetime | None:
    if not path.exists():
        return None
    last = None
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            ts = _parse_ts(row.get("timestamp_utc", ""))
            if ts:
                last = ts
    return last


def liveness_alert(args: argparse.Namespace) -> str | None:
    if not args.window_active and os.getenv("DREAMLEAGUE_ACTIVE", "false").lower() not in {"1", "true", "yes"}:
        return None

    now = datetime.now(timezone.utc)
    last = _last_csv_timestamp(Path(args.live_attempts))
    age_hours = float("inf") if last is None else (now - last).total_seconds() / 3600.0
    if age_hours < args.max_idle_hours:
        return None

    state_path = Path(args.state)
    today = now.date().isoformat()
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("liveness_alert_date") == today:
                return None
        except json.JSONDecodeError:
            pass

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"liveness_alert_date": today}, indent=2), encoding="utf-8")
    last_s = last.isoformat(timespec="seconds") if last else "never"
    return (
        "Dota live bot liveness alert\n"
        f"No live_attempts.csv write for {age_hours:.1f}h\n"
        f"Last attempt: {last_s}\n"
        f"Threshold: {args.max_idle_hours:.1f}h"
    )


def daily_summary(args: argparse.Namespace) -> str:
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=args.hours)
    attempts = _read_recent(Path(args.live_attempts), since)
    exits = _read_recent(Path(args.live_exits), since)

    submit_rows = [r for r in attempts if (r.get("phase") or "submit") == "submit"]
    filled_usd = sum(_f(r.get("filled_size_usd")) for r in submit_rows)
    submitted_usd = sum(_f(r.get("submitted_size_usd")) for r in submit_rows)
    by_event: dict[str, float] = defaultdict(float)
    statuses = Counter()
    for row in submit_rows:
        by_event[row.get("event_type") or "UNKNOWN"] += _f(row.get("filled_size_usd"))
        statuses[row.get("order_status") or ""] += 1

    lines = [
        "Dota live bot daily summary",
        f"Window: last {args.hours:.0f}h",
        f"Signals attempted: {len(submit_rows)}",
        f"Submitted: ${submitted_usd:.2f}",
        f"Confirmed filled: ${filled_usd:.2f}",
        f"Exit rows: {len(exits)}",
    ]
    if statuses:
        lines.append("Statuses: " + ", ".join(f"{k}:{v}" for k, v in statuses.most_common(5)))
    if by_event:
        lines.append("Filled by event: " + ", ".join(f"{k}:${v:.2f}" for k, v in sorted(by_event.items())))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["liveness", "daily"])
    parser.add_argument("--live-attempts", default="logs/live_attempts.csv")
    parser.add_argument("--live-exits", default="logs/live_exits.csv")
    parser.add_argument("--state", default="logs/telegram_ops_state.json")
    parser.add_argument("--max-idle-hours", type=float, default=6.0)
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--window-active", action="store_true")
    parser.add_argument("--require-send", action="store_true")
    args = parser.parse_args()

    text = liveness_alert(args) if args.mode == "liveness" else daily_summary(args)
    if not text:
        return
    send_telegram(text, require_send=args.require_send)


if __name__ == "__main__":
    main()
