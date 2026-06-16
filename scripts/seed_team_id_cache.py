"""Seed logs/team_id_cache.json from OpenDota's teams endpoint.

OpenDota tracks every pro team_id ↔ team_name pair. Loading it as a one-time
seed gives the bot's auto-mapping (in main.py via team_id_cache.backfill_team_names)
a working dictionary BEFORE any Steam request happens to populate a name.

Run once: python3 scripts/seed_team_id_cache.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / "logs" / "team_id_cache.json"
OPENDOTA_URL = "https://api.opendota.com/api/teams"

# Known team-name aliases that the bot's normalizer handles. We use the
# canonical name from OpenDota; team_utils.norm_team() will collapse to
# whatever the markets.yaml entries use.


async def main():
    # Force gzip-only (avoid brotli encoding mismatch on this aiohttp build)
    headers = {"user-agent": "Mozilla/5.0", "Accept-Encoding": "gzip, deflate"}
    async with aiohttp.ClientSession() as s:
        async with s.get(OPENDOTA_URL, timeout=30, headers=headers) as r:
            r.raise_for_status()
            text = await r.text()
            teams = json.loads(text)
    print(f"OpenDota returned {len(teams)} teams")

    # Load existing cache (preserve any Steam-observed entries)
    existing: dict[str, str] = {}
    if CACHE_PATH.exists():
        try:
            existing = json.loads(CACHE_PATH.read_text())
            print(f"Existing cache has {len(existing)} entries — preserving them")
        except Exception:
            existing = {}

    added = updated = 0
    for t in teams:
        tid = t.get("team_id")
        name = (t.get("name") or "").strip()
        if not tid or not name: continue
        tid_str = str(tid)
        if tid_str in existing:
            if existing[tid_str] == name: continue
            existing[tid_str] = name; updated += 1
        else:
            existing[tid_str] = name; added += 1

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(existing, sort_keys=True))
    print(f"\nCache written: {CACHE_PATH}")
    print(f"  added:   {added}")
    print(f"  updated: {updated}")
    print(f"  total:   {len(existing)}")

    # Quick spot-check for teams we care about
    print("\nSpot check:")
    for needle in ["LGD", "Aurora", "Falcons", "Tundra", "Xtreme", "PARIVISION",
                    "BetBoom", "Spirit", "Liquid", "HEROIC", "Yandex", "OG"]:
        matches = [(tid, n) for tid, n in existing.items() if needle.lower() in n.lower()]
        for tid, n in matches[:2]:
            print(f"  {tid:>10s}  {n}")


if __name__ == "__main__":
    asyncio.run(main())
