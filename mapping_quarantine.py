from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def is_quarantined(mapping: dict, *, now: datetime | None = None) -> bool:
    if mapping.get("mapping_state") != "quarantined":
        return False
    until = _parse_dt(mapping.get("quarantined_until"))
    if until is None:
        return True
    return until > (now or utc_now())


def quarantine_mapping(
    mapping: dict,
    reason: str,
    *,
    hours: float = 24,
    now: datetime | None = None,
) -> dict:
    ts = now or utc_now()
    mapping["mapping_state"] = "quarantined"
    mapping["quarantine_reason"] = reason
    mapping["quarantined_at_utc"] = ts.isoformat(timespec="seconds")
    mapping["quarantined_until"] = (ts + timedelta(hours=hours)).isoformat(timespec="seconds")
    return mapping


def clear_quarantine(mapping: dict) -> dict:
    for key in ("mapping_state", "quarantine_reason", "quarantined_at_utc", "quarantined_until"):
        mapping.pop(key, None)
    return mapping


def _matches_identifier(mapping: dict, identifier: str) -> bool:
    ident = str(identifier)
    return ident in {
        str(mapping.get("market_id") or ""),
        str(mapping.get("condition_id") or ""),
        str(mapping.get("yes_token_id") or ""),
        str(mapping.get("no_token_id") or ""),
        str(mapping.get("dota_match_id") or ""),
    }


def quarantine_in_file(
    path: str | Path,
    identifier: str,
    reason: str,
    *,
    hours: float = 24,
) -> bool:
    p = Path(path)
    data = yaml.safe_load(p.read_text()) if p.exists() else {"markets": []}
    markets = data.get("markets", []) if isinstance(data, dict) else []
    changed = False
    for mapping in markets:
        if _matches_identifier(mapping, identifier):
            quarantine_mapping(mapping, reason, hours=hours)
            changed = True
    if changed:
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False))
        tmp.replace(p)
    return changed
