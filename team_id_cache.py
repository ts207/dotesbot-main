"""Team-ID → team-name cache for backfilling missing Steam metadata.

Problem: Valve's GetTopLiveGame / GetLiveLeagueGames sometimes returns:
  - team_id populated (e.g. team_id_radiant=7119388)
  - team_name EMPTY ("")

This breaks the fuzzy-team-name matcher in sync_markets.match_direction(),
which is the gate for auto-linking unmapped markets to live Steam matches.

Fix: maintain a persistent cache of {team_id → team_name} populated from
games where BOTH are present. When a game arrives with team_id but empty
team_name, look up the cached name and inject it before auto-mapping runs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable


_CACHE_PATH = Path(os.getenv("TEAM_ID_CACHE_PATH", "logs/team_id_cache.json"))
_cache: dict[str, str] = {}
_dirty = False


def _load() -> None:
    global _cache
    try:
        if _CACHE_PATH.exists():
            _cache = {str(k): str(v) for k, v in
                       json.loads(_CACHE_PATH.read_text()).items() if v}
    except Exception:
        _cache = {}


def _save() -> None:
    """Persist cache. MERGES with on-disk state to preserve entries written by
    other processes (e.g. scripts/seed_team_id_cache.py). Our in-memory writes
    take precedence on conflict — they're newer."""
    global _dirty, _cache
    if not _dirty: return
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Re-read disk, merge, write
        disk: dict[str, str] = {}
        if _CACHE_PATH.exists():
            try:
                disk = {str(k): str(v) for k, v in
                         json.loads(_CACHE_PATH.read_text()).items() if v}
            except Exception:
                disk = {}
        merged = dict(disk)
        merged.update(_cache)  # our memory wins on conflict
        _cache = merged
        _CACHE_PATH.write_text(json.dumps(merged, sort_keys=True))
        _dirty = False
    except Exception:
        pass


_load()


def observe_game(game: dict) -> None:
    """If both team_id and team_name are present on this game, cache them.
    Idempotent and cheap."""
    global _dirty
    for side in ("radiant", "dire"):
        tid = str(game.get(f"{side}_team_id") or "")
        name = str(game.get(f"{side}_team") or "")
        # Some game dicts use raw Steam keys
        if not tid: tid = str(game.get(f"team_id_{side}") or "")
        if not name: name = str(game.get(f"team_name_{side}") or "")
        if tid and name and _cache.get(tid) != name:
            _cache[tid] = name
            _dirty = True
    if _dirty:
        _save()


def backfill_team_names(game: dict) -> dict:
    """Mutate game in place: if team_name is empty but team_id is known,
    look up the cached name. Returns the same dict for chaining."""
    for side in ("radiant", "dire"):
        # Standard keys after normalization
        name_key = f"{side}_team"
        id_key = f"{side}_team_id"
        if not game.get(name_key):
            tid = str(game.get(id_key) or "")
            if tid and _cache.get(tid):
                game[name_key] = _cache[tid]
        # Raw Steam keys (in case caller hasn't normalized yet)
        raw_name = f"team_name_{side}"
        raw_id = f"team_id_{side}"
        if not game.get(raw_name):
            tid = str(game.get(raw_id) or "")
            if tid and _cache.get(tid):
                game[raw_name] = _cache[tid]
    return game


def observe_many(games: Iterable[dict]) -> None:
    for g in games:
        observe_game(g)


def cache_size() -> int:
    return len(_cache)
