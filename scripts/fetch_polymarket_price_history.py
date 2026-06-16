#!/usr/bin/env python3
"""Fetch Polymarket CLOB price history for candidate market tokens."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp


CLOB_PRICE_HISTORY_URL = "https://clob.polymarket.com/prices-history"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def parse_ts_to_unix(value: str | None) -> int | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return None


def load_payload(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def append_ledger(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, separators=(",", ":"), sort_keys=True) + "\n")


def token_jobs(rows: list[dict[str, str]], include_locked: bool) -> list[dict[str, str]]:
    jobs = []
    seen: set[str] = set()
    for row in rows:
        if not include_locked and str(row.get("is_locked_execution_audit", "")).lower() == "true":
            continue
        token_id = row.get("yes_token_id") or row.get("token_id_yes") or ""
        if not token_id:
            jobs.append(
                {
                    "market_id": row.get("market_id", ""),
                    "condition_id": row.get("condition_id", ""),
                    "event_id": row.get("event_id", ""),
                    "slug": row.get("slug", ""),
                    "question": row.get("question", ""),
                    "source_universe": row.get("source_universe", ""),
                    "token_id": "",
                    "requested_start_ts": "",
                    "requested_end_ts": "",
                }
            )
            continue
        if token_id in seen:
            continue
        seen.add(token_id)
        jobs.append(
            {
                "market_id": row.get("market_id", ""),
                "condition_id": row.get("condition_id", ""),
                "event_id": row.get("event_id", ""),
                "slug": row.get("slug", ""),
                "question": row.get("question", ""),
                "source_universe": row.get("source_universe", ""),
                "token_id": token_id,
                "requested_start_ts": str(parse_ts_to_unix(row.get("start_ts")) or ""),
                "requested_end_ts": str(parse_ts_to_unix(row.get("closed_ts") or row.get("end_ts")) or ""),
            }
        )
    return jobs


def history_points(payload: Any) -> list:
    if isinstance(payload, dict):
        history = payload.get("history") or []
    elif isinstance(payload, list):
        history = payload
    else:
        history = []
    return history if isinstance(history, list) else []


def classify_history_status(http_status: int | None, data: Any, error: str | None, token_id: str) -> str:
    if not token_id:
        return "wrong_or_missing_token_id"
    if http_status in {429, 520, 521, 522, 523, 524}:
        return "rate_limited_or_throttled"
    if error:
        return "error"
    points = history_points(data)
    if not points:
        return "empty"
    return "ok"


def base_envelope(job: dict[str, str], interval: str, fidelity: str) -> dict[str, Any]:
    return {
        "token_id": job["token_id"],
        "market_id": job.get("market_id", ""),
        "condition_id": job.get("condition_id", ""),
        "event_id": job.get("event_id", ""),
        "slug": job.get("slug", ""),
        "question": job.get("question", ""),
        "source_universe": job.get("source_universe", ""),
        "requested_start_ts": job.get("requested_start_ts", ""),
        "requested_end_ts": job.get("requested_end_ts", ""),
        "requested_interval": interval,
        "requested_fidelity": fidelity,
        "fetch_status": "error",
        "fetched_at": utc_now(),
        "raw_response": {},
    }


async def fetch_one(
    session: aiohttp.ClientSession,
    job: dict[str, str],
    *,
    interval: str,
    fidelity: str,
    ledger_path: Path,
    max_retries: int,
) -> dict[str, Any]:
    envelope = base_envelope(job, interval, fidelity)
    if not job.get("token_id"):
        envelope["fetch_status"] = "wrong_or_missing_token_id"
        envelope["error"] = "missing token_id"
        append_ledger(
            ledger_path,
            ledger_entry(job, {}, envelope["fetch_status"], None, 0, envelope["error"]),
        )
        return envelope

    params = {"market": job["token_id"], "interval": interval, "fidelity": fidelity}
    if job.get("requested_start_ts"):
        params["startTs"] = job["requested_start_ts"]
    if job.get("requested_end_ts"):
        params["endTs"] = job["requested_end_ts"]
    headers = {"Accept-Encoding": "gzip, deflate", "User-Agent": "curl/8"}
    for attempt in range(max_retries + 1):
        http_status = None
        error = None
        data: Any = {}
        try:
            async with session.get(CLOB_PRICE_HISTORY_URL, params=params, headers=headers, timeout=30) as resp:
                http_status = resp.status
                envelope["http_status"] = resp.status
                if resp.status != 200:
                    error = (await resp.text())[:500]
                else:
                    data = await resp.json()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        status = classify_history_status(http_status, data, error, job["token_id"])
        if status == "rate_limited_or_throttled" and attempt < max_retries:
            append_ledger(ledger_path, ledger_entry(job, params, status, http_status, 0, error))
            await asyncio.sleep(2**attempt)
            continue
        envelope["raw_response"] = data
        envelope["fetch_status"] = status
        if error:
            envelope["error"] = error
        append_ledger(ledger_path, ledger_entry(job, params, status, http_status, len(history_points(data)), error))
        return envelope
    return envelope


def ledger_entry(
    job: dict[str, str],
    params: dict[str, Any],
    status: str,
    http_status: int | None,
    records_returned: int,
    error: str | None,
) -> dict[str, Any]:
    return {
        "script": Path(__file__).name,
        "request_url_or_endpoint": CLOB_PRICE_HISTORY_URL,
        "params": params,
        "status": status,
        "http_status": http_status,
        "fetched_at": utc_now(),
        "records_returned": records_returned,
        "error": error,
        "market_id": job.get("market_id", ""),
        "condition_id": job.get("condition_id", ""),
        "token_id": job.get("token_id", ""),
        "source_universe": job.get("source_universe", ""),
    }


def write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def existing_ok(path: Path) -> bool:
    payload = load_payload(path)
    return bool(payload and payload.get("fetch_status") == "ok")


async def run(args: argparse.Namespace) -> None:
    rows = read_csv(Path(args.market_universe))
    jobs = token_jobs(rows, include_locked=args.include_locked)
    out_dir = Path(args.output_dir)
    statuses = Counter()
    source_counts = Counter()
    async with aiohttp.ClientSession() as session:
        for idx, job in enumerate(jobs, start=1):
            if not job.get("token_id"):
                payload = base_envelope(job, args.interval, args.fidelity)
                payload["fetch_status"] = "wrong_or_missing_token_id"
                payload["error"] = "missing token_id"
                statuses[payload["fetch_status"]] += 1
                source_counts[job.get("source_universe") or "unknown"] += 1
                continue
            path = out_dir / f"{job['token_id']}.json"
            if not args.refresh and existing_ok(path):
                payload = load_payload(path) or {}
                status = str(payload.get("fetch_status") or "ok")
            else:
                payload = await fetch_one(
                    session,
                    job,
                    interval=args.interval,
                    fidelity=args.fidelity,
                    ledger_path=Path(args.fetch_ledger),
                    max_retries=args.max_retries,
                )
                write_payload(path, payload)
                await asyncio.sleep(args.sleep_sec)
                status = str(payload.get("fetch_status") or "error")
            statuses[status] += 1
            source_counts[job.get("source_universe") or "unknown"] += 1
            if idx % 10 == 0 or idx == len(jobs):
                print(f"progress={idx}/{len(jobs)}")
    audit = {
        "candidate_tokens": len([j for j in jobs if j.get("token_id")]),
        "status_counts": dict(statuses),
        "source_universe_counts": dict(source_counts),
        "fetched_ok": statuses.get("ok", 0),
        "empty_history": statuses.get("empty", 0),
        "partial_history": statuses.get("partial", 0),
        "error": statuses.get("error", 0),
        "rate_limited_or_throttled": statuses.get("rate_limited_or_throttled", 0),
        "wrong_or_missing_token_id": statuses.get("wrong_or_missing_token_id", 0),
    }
    Path(args.audit_output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.audit_output).write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(audit, indent=2, sort_keys=True))
    print(f"wrote {out_dir}")
    print(f"wrote {args.audit_output}")
    print(f"wrote {args.fetch_ledger}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-universe", default="data/processed/polymarket/dota_market_universe.csv")
    parser.add_argument("--output-dir", default="data/raw/polymarket/price_history")
    parser.add_argument("--audit-output", default="reports/price_history_fetch_audit.json")
    parser.add_argument("--fetch-ledger", default="logs/polymarket_price_history_fetches.jsonl")
    parser.add_argument("--interval", default="all")
    parser.add_argument("--fidelity", default="60")
    parser.add_argument("--sleep-sec", type=float, default=0.25)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--include-locked", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
