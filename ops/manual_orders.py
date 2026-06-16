"""Manual-order IPC queue: dashboard appends, bot drains.

Append-only JSONL file at ``logs/manual_orders.jsonl``. Producer (dashboard)
writes orders with ``enqueue``; consumer (bot's live executor) calls ``drain``
once per tick to read newly-added rows and mark them processed.

Format (one JSON object per line):
    {
        "id": "<uuid4>",
        "ts": "<utc iso8601>",
        "action": "buy" | "exit",
        "match_id": "8830491659",
        "token_id": "12345...",         # YES or NO token
        "side": "yes" | "no",            # only for buy
        "size_usd": 50.0,                # only for buy
        "price_cap": 0.65,               # only for buy (optional)
        "processed_at": null,             # bot fills when processed
        "result": null                    # bot fills with status / fill details
    }
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

QUEUE_PATH = Path("logs") / "manual_orders.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def enqueue(order: dict[str, Any]) -> str:
    """Append an order to the queue, return assigned id."""
    if "id" not in order:
        order["id"] = uuid.uuid4().hex
    order["ts"] = _now()
    order.setdefault("processed_at", None)
    order.setdefault("result", None)
    QUEUE_PATH.parent.mkdir(exist_ok=True)
    with QUEUE_PATH.open("a") as fp:
        fp.write(json.dumps(order) + "\n")
    return order["id"]


def drain() -> list[dict[str, Any]]:
    """Read & return all orders that haven't been processed. Caller must
    invoke ``mark_processed`` after handling each, to persist completion."""
    if not QUEUE_PATH.exists():
        return []
    out: list[dict[str, Any]] = []
    with QUEUE_PATH.open() as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("processed_at") is None:
                out.append(row)
    return out


def mark_processed(order_id: str, result: dict[str, Any]) -> None:
    """Rewrite the queue file with the matching row marked done."""
    if not QUEUE_PATH.exists():
        return
    lines: list[str] = []
    with QUEUE_PATH.open() as fp:
        for line in fp:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError:
                lines.append(line)
                continue
            if row.get("id") == order_id and row.get("processed_at") is None:
                row["processed_at"] = _now()
                row["result"] = result
            lines.append(json.dumps(row) + "\n")
    tmp = QUEUE_PATH.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(lines))
    os.replace(tmp, QUEUE_PATH)
