"""runtime_markets.py — Compaction and cleanup logic for runtime market overlay."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

ALLOWED_RUNTIME_FIELDS = {
    "dota_match_id", "confidence", "auto_mapped_at_utc",
    "auto_mapped_source", "steam_radiant_team", "steam_dire_team",
    "steam_side_mapping", "current_game_number",
    "series_score_yes", "series_score_no", "p_next_yes",
    "quarantined", "quarantine_reason"
}

# Identity fields that define a market
IDENTITY_FIELDS = {
    "market_id", "condition_id", "yes_token_id", "no_token_id",
    "yes_team", "no_team", "market_type", "source_url"
}

def runtime_market_key(m: dict) -> str | None:
    """Extract a stable key for deduplicating market data."""
    key = m.get("condition_id") or m.get("yes_token_id")
    return str(key) if key else None

def parse_runtime_timestamp(value: Any) -> datetime | None:
    """Parse auto_mapped_at_utc into a timezone-aware datetime."""
    if not value or not isinstance(value, str):
        return None
    try:
        # Handle '2026-06-17T04:26:37+00:00' or similar
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None

def compact_runtime_markets(
    runtime_markets: list[dict],
    base_markets: list[dict],
    *,
    now: datetime | None = None,
    runtime_only_ttl_hours: int = 72,
    mapped_ttl_hours: int = 168,
) -> tuple[list[dict], dict[str, int]]:
    """Deduplicate and prune stale entries from the runtime overlay.
    
    Returns (compacted_list, stats).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    
    stats = {
        "before": len(runtime_markets),
        "after": 0,
        "removed": 0,
        "deduplicated": 0,
        "runtime_only_removed": 0,
        "noop_removed": 0,
    }

    base_keys = {runtime_market_key(m) for m in base_markets if runtime_market_key(m)}
    
    # 1. Group by key and keep the 'best' one deterministically
    by_key: dict[str, list[dict]] = {}
    for m in runtime_markets:
        key = runtime_market_key(m)
        if key:
            by_key.setdefault(key, []).append(m)
        else:
            stats["removed"] += 1 # Entry without identity key is useless
    
    deduplicated: list[dict] = []
    for key, group in by_key.items():
        if len(group) > 1:
            stats["deduplicated"] += len(group) - 1
            # Sort group to find the winner:
            # 1. higher confidence
            # 2. newer auto_mapped_at_utc
            # 3. has dota_match_id
            # 4. later in original list (original order preserved in group)
            def sort_score(m: dict) -> tuple[float, float, bool]:
                conf = 0.0
                try:
                    conf = float(m.get("confidence", 0.0))
                except (ValueError, TypeError):
                    pass
                ts = parse_runtime_timestamp(m.get("auto_mapped_at_utc"))
                ts_val = ts.timestamp() if ts else 0.0
                has_match_id = bool(m.get("dota_match_id") and "STEAM_MATCH" not in str(m.get("dota_match_id")))
                return (conf, ts_val, has_match_id)
            
            group.sort(key=sort_score, reverse=True)
            winner = group[0]
        else:
            winner = group[0]
        deduplicated.append(winner)

    # 2. Filter by TTL and relevance
    final: list[dict] = []
    for m in deduplicated:
        key = runtime_market_key(m)
        is_runtime_only = key not in base_keys
        
        match_id = m.get("dota_match_id")
        has_real_match_id = bool(match_id and "STEAM_MATCH" not in str(match_id))
        
        conf = 0.0
        try:
            conf = float(m.get("confidence", 0.0))
        except (ValueError, TypeError):
            pass
            
        ts = parse_runtime_timestamp(m.get("auto_mapped_at_utc"))
        
        # Retention window selection
        ttl = timedelta(hours=runtime_only_ttl_hours if is_runtime_only else mapped_ttl_hours)
        is_fresh = ts is not None and (now - ts) < ttl
        
        # Policy: Keep if active match, high confidence, fresh, or relevant quarantine
        keep = False
        if has_real_match_id:
            keep = True
        elif conf >= 0.98:
            keep = True
        elif is_fresh:
            keep = True
        elif m.get("quarantined") or m.get("quarantine_reason"):
            # If it's a base market overlay with a quarantine, keep it
            if not is_runtime_only:
                keep = True
        elif not is_runtime_only:
            # If it's a base market overlay but has no-op fields, we'll check it next
            # For now, assume we might keep it unless it's a no-op
            keep = True
            
        if not keep:
            stats["removed"] += 1
            if is_runtime_only:
                stats["runtime_only_removed"] += 1
            continue

        # 3. No-op check for base market overlays
        if not is_runtime_only:
            has_runtime_data = any(m.get(f) is not None for f in ALLOWED_RUNTIME_FIELDS)
            if not has_runtime_data:
                stats["removed"] += 1
                stats["noop_removed"] += 1
                continue

        final.append(m)

    stats["after"] = len(final)
    return final, stats
