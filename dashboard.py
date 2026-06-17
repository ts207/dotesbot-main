"""Unified Aegis Strategic Command Dashboard — Single File Institutional UI.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))

from aiohttp import web

from live_executor import LiveCLOBClient
from config import (
    CSV_LOG_PATH,
    PAPER_TRADES_CSV_PATH,
    DOTA_EVENTS_CSV_PATH,
    BOOK_EVENTS_CSV_PATH,
    BOOK_REFRESH_RESCUE_CSV_PATH,
    RICH_CONTEXT_CSV_PATH,
    LIVE_ATTEMPTS_CSV_PATH,
)
from mapping import load_valid_mappings
from hero_data import HERO_ID_MAP

MATCH_WINNER_CSV_PATH = os.path.join("logs", "match_winner_signals.csv")
RAW_SNAPSHOTS_CSV_PATH = os.path.join("logs", "raw_snapshots.csv")
LIVE_EXITS_CSV_PATH = os.path.join("logs", "live_exits.csv")
USDC_BALANCE_JSON_PATH = os.path.join("logs", "usdc_balance.json")

_FEED_ROWS = 25   # rows shown per feed
_EXIT_ROWS = 30   # closed positions shown
_PRICE_ROWS = 40  # prices shown
_LIVE_GAME_MAX_AGE_SEC = 300

RICH_CONTEXT_CSV_PATH = "logs/rich_context.csv"
RAW_SNAPSHOTS_CSV_PATH = "logs/raw_snapshots.csv"
_HEALTH_STALE_SEC = 120


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def _read_csv(path: str | Path, tail_lines: int | None = None) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        if tail_lines and p.stat().st_size > 1_000_000:
            import subprocess
            cmd = ["tail", "-n", str(tail_lines), str(p)]
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
            if proc.returncode == 0:
                # Need header for DictReader
                with p.open(encoding="utf-8") as f:
                    header = f.readline()
                from io import StringIO
                return list(csv.DictReader(StringIO(header + proc.stdout)))
        
        with p.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def _get_mapped_match_ids() -> set[str]:
    try:
        valid, _ = load_valid_mappings()
        mapped = set()
        for m in valid:
            mid = str(m.get("dota_match_id") or "")
            if mid and not mid.startswith("STEAM_"):
                mapped.add(mid)
        return mapped
    except Exception:
        return set()


def _read_json(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _fnum(v) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_utc_ts(v) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _truthy(v) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes"}


def _age_sec(v) -> int | None:
    ts = _parse_utc_ts(v)
    if not ts:
        return None
    return max(0, int((datetime.now(timezone.utc) - ts).total_seconds()))


def _latest_timestamp(rows: list[dict], *keys: str) -> str:
    latest = ""
    for row in rows:
        for key in keys:
            value = row.get(key) or ""
            if value > latest:
                latest = value
    return latest


def _health_item(label: str, rows: list[dict], *ts_keys: str) -> dict:
    ts = _latest_timestamp(rows, *ts_keys)
    age = _age_sec(ts)
    return {
        "label": label,
        "count": len(rows),
        "latest_ts": ts,
        "age_sec": age,
        "status": "empty" if not rows else "stale" if age is None or age > _HEALTH_STALE_SEC else "fresh",
    }


async def _live_health() -> dict:
    import time as _time
    from storage_v2 import StorageV2
    attempts = _read_csv(LIVE_ATTEMPTS_CSV_PATH, tail_lines=1000)
    exits = _read_csv(LIVE_EXITS_CSV_PATH, tail_lines=1000)
    balance_data = _read_json(USDC_BALANCE_JSON_PATH)
    
    storage = StorageV2()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    from live_state import live_state_mode
    budget = storage.load_daily_budget(today, mode=live_state_mode()) or {}
    positions = storage.load_positions(mode="live")
    if not isinstance(positions, list):
        positions = []

    # Attempt to fetch real-time balance from CLOB if credentials exist
    real_balance = None
    from config import ENABLE_REAL_LIVE_TRADING
    if os.getenv("POLY_PRIVATE_KEY") or os.getenv("PK"):
        if ENABLE_REAL_LIVE_TRADING:
            try:
                client = LiveCLOBClient()
                real_balance = await client.get_usdc_balance()
            except Exception:
                pass
        else:
            real_balance = StorageV2().get_simulated_balance(1000.0)

    submit_rows = [r for r in attempts if (r.get("phase") or "submit") == "submit"]
    resolution_rows = [r for r in attempts if (r.get("phase") or "") == "resolution"]
    latest_attempt = _latest_timestamp(attempts, "timestamp_utc")
    active_positions = [
        p for p in positions
        if isinstance(p, dict) and p.get("state") in {"OPEN", "PARTIALLY_EXITED", "PENDING_ENTRY", "PENDING_EXIT_GTC", "EXITING"}
    ]
    live_state_open = int(budget.get("open_positions", 0))
    drift_count = abs(live_state_open - len(active_positions))
    submitted_usd = sum(_fnum(r.get("submitted_size_usd")) or 0.0 for r in submit_rows)
    filled_usd = sum(_fnum(r.get("filled_size_usd")) or 0.0 for r in submit_rows + resolution_rows)
    
    # Use real-time balance if fetched, otherwise fall back to log
    usdc_balance = real_balance if real_balance is not None else _fnum(balance_data.get("usdc_balance"))
    usdc_checked_at_ns = _time.time_ns() if real_balance is not None else _fnum(balance_data.get("checked_at_ns"))
    
    usdc_age_sec = (
        int((_time.time_ns() - int(usdc_checked_at_ns)) / 1e9)
        if usdc_checked_at_ns else None
    )
    return {
        "attempts": len(submit_rows),
        "resolutions": len(resolution_rows),
        "exits": max(0, len(exits) - 1 if exits and exits[0].get("position_id") == "STARTUP_HEARTBEAT" else len(exits)),
        "submitted_usd": round(submitted_usd, 2),
        "filled_usd": round(filled_usd, 2),
        "latest_attempt_ts": latest_attempt,
        "latest_attempt_age_sec": _age_sec(latest_attempt),
        "state_open_positions": live_state_open,
        "store_active_positions": len(active_positions),
        "drift_count": drift_count,
        "state_total_filled_usd": round(float(budget.get("total_filled_usd") or 0.0), 2),
        "state_total_submitted_usd": round(float(budget.get("total_submitted_usd") or 0.0), 2),
        "usdc_balance": round(usdc_balance, 2) if usdc_balance is not None else 0.0,
        "usdc_balance_age_sec": usdc_age_sec,
        "is_mock_balance": real_balance is None and usdc_balance == 10.0 and usdc_age_sec is not None and usdc_age_sec > 3600,
        "is_live_on_chain": ENABLE_REAL_LIVE_TRADING and real_balance is not None,
    }


# ---------------------------------------------------------------------------
# Data builders
# ---------------------------------------------------------------------------

def _session_data(trades: list[dict]) -> dict:
    entries = [r for r in trades if (r.get("action") or "").strip().lower() == "entry"]
    exits   = [r for r in trades if (r.get("action") or "").strip().lower() == "exit"]
    pnls    = [_fnum(r.get("pnl_usd")) for r in exits]
    
    # Build PnL History (Equity Curve)
    pnl_history = [0.0]
    current_pnl = 0.0
    for p in pnls:
        if p is not None:
            current_pnl += p
            pnl_history.append(round(current_pnl, 2))

    pnls    = [x for x in pnls if x is not None]
    wins    = sum(1 for x in pnls if x > 0)
    total   = sum(pnls) if pnls else 0.0
    costs   = [_fnum(r.get("cost_usd")) for r in entries]
    costs   = [x for x in costs if x is not None]
    return {
        "total_entries": len(entries),
        "total_exits":   len(exits),
        "open_count":    max(len(entries) - len(exits), 0),
        "total_pnl":     round(total, 4),
        "win_rate":      round(wins / len(pnls) * 100, 1) if pnls else None,
        "wins":          wins,
        "losses":        len(pnls) - wins,
        "notional_usd":  round(sum(costs), 2) if costs else 0.0,
        "pnl_history":   pnl_history,
    }


def _open_positions(trades: list[dict]) -> list[dict]:
    """Reconstruct open positions from the entry/exit log (FIFO per token)."""
    open_by_token: dict[str, list[dict]] = {}
    for row in trades:
        action   = (row.get("action") or "").strip().lower()
        token_id = str(row.get("token_id") or "")
        if not token_id:
            continue
        if action == "entry":
            open_by_token.setdefault(token_id, []).append(row)
        elif action == "exit":
            bucket = open_by_token.get(token_id)
            if bucket:
                bucket.pop(0)
                if not bucket:
                    del open_by_token[token_id]
    return [row for bucket in open_by_token.values() for row in bucket]


def _closed_positions(trades: list[dict], n: int) -> list[dict]:
    exits = [r for r in trades if (r.get("action") or "").strip().lower() == "exit"]
    return list(reversed(exits[-n:]))


def _latest_prices() -> list[dict]:
    book_rows = _read_csv(BOOK_EVENTS_CSV_PATH, tail_lines=2000)
    if not book_rows:
        return []
    valid, _ = load_valid_mappings()
    token_info: dict[str, dict] = {}
    for m in valid:
        name = (m.get("name") or "").replace("Dota 2: ", "")
        mt = m.get("market_type", "")
        suffix = " (BO3)" if mt == "MATCH_WINNER" else ""
        for tid_key in ("yes_token_id", "no_token_id"):
            tid = str(m.get(tid_key, ""))
            if tid:
                side = "YES" if tid_key == "yes_token_id" else "NO"
                team = m.get("yes_team" if tid_key == "yes_token_id" else "no_team", "")
                token_info[tid] = {"market": name + suffix, "side": side, "team": team, "mt": mt}
    latest: dict[str, dict] = {}
    for r in book_rows:
        tid = str(r.get("asset_id", ""))
        if not tid:
            continue
        latest[tid] = dict(r)
        latest[tid]["_info"] = token_info.get(tid, {})
    rows = []
    for tid, r in latest.items():
        info = r.pop("_info", {})
        if not info:
            continue
        rows.append({
            "market": info.get("market", tid[:12]),
            "side": info.get("side", "?"),
            "team": info.get("team", ""),
            "mt": info.get("mt", ""),
            "bid": r.get("best_bid", ""),
            "ask": r.get("best_ask", ""),
            "mid": r.get("mid", ""),
            "spread": r.get("spread", ""),
            "ask_size": r.get("ask_size", ""),
            "ts": r.get("timestamp_utc", ""),
            "age_sec": _age_sec(r.get("timestamp_utc")),
            "token_id": tid,
        })
    for row in rows:
        age = row.get("age_sec")
        row["status"] = "stale" if age is None or age > _HEALTH_STALE_SEC else "fresh"
    rows.sort(key=lambda x: (
        1 if x.get("status") == "stale" else 0,
        x.get("age_sec") if x.get("age_sec") is not None else 10**9,
        x.get("market", ""),
        x.get("side", ""),
    ))
    return rows[:_PRICE_ROWS]


def _data_health(
    raw_rows: list[dict],
    signal_rows: list[dict],
    event_rows: list[dict],
    book_rows: list[dict],
    live_games: list[dict],
) -> dict:
    items = [
        _health_item("TopLive", [r for r in raw_rows if r.get("data_source") == "top_live"], "received_at_utc"),
        _health_item("LiveLeague", _read_csv(RICH_CONTEXT_CSV_PATH), "timestamp_utc"),
        _health_item("Signals", signal_rows, "timestamp_utc"),
        _health_item("Events", event_rows, "timestamp_utc"),
        _health_item("Books", book_rows, "timestamp_utc"),
    ]
    live_count = len(live_games)
    stale_count = sum(1 for item in items if item["status"] == "stale")
    fresh_count = sum(1 for item in items if item["status"] == "fresh")
    if live_count:
        mode = "live"
    elif fresh_count:
        mode = "idle"
    else:
        mode = "stale"
    return {
        "mode": mode,
        "live_count": live_count,
        "fresh_count": fresh_count,
        "stale_count": stale_count,
        "items": items,
    }


_TOWER_NAMES = {
    0: "T1T", 1: "T1M", 2: "T1B",
    3: "T2T", 4: "T2M", 5: "T2B",
    6: "T3T", 7: "T3M", 8: "T3B",
    9: "T4T", 10: "T4B",
}

def _tower_bits_to_str(state_val) -> str:
    try:
        bits = int(float(state_val))
    except (TypeError, ValueError):
        return "—"
    alive = []
    for bit, name in _TOWER_NAMES.items():
        if bits & (1 << bit):
            alive.append(name)
    return " ".join(alive) if alive else "0"

def _towers_alive(state_val) -> int:
    try:
        bits = int(float(state_val))
    except (TypeError, ValueError):
        return -1
    return bin(bits).count("1")

def _to_int(v, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _latest_liveleague_by_match() -> dict[str, dict]:
    by_match: dict[str, dict] = {}
    min_ts = datetime.now(timezone.utc).timestamp() - _LIVE_GAME_MAX_AGE_SEC
    for r in _read_csv(RICH_CONTEXT_CSV_PATH, tail_lines=5000):
        mid = r.get("match_id") or r.get("lobby_id") or ""
        if not mid:
            continue
        ts = _parse_utc_ts(r.get("timestamp_utc"))
        if not ts or ts.timestamp() < min_ts:
            continue
        prev = by_match.get(mid)
        if prev and (r.get("timestamp_utc") or "") <= (prev.get("timestamp_utc") or ""):
            continue
        by_match[mid] = r
    return by_match


def _read_finished_match_ids() -> set[str]:
    """Match IDs whose most recent raw_snapshots row has game_over=True.

    Rich_context can keep writing rows after a game ends (the delayed
    realtime stream ticks longer than the actual game), so this acts as the
    authoritative end-of-game signal.
    """
    latest_by_match: dict[str, tuple[str, bool]] = {}
    for r in _read_csv(RAW_SNAPSHOTS_CSV_PATH, tail_lines=5000):
        mid = r.get("match_id") or r.get("lobby_id") or ""
        if not mid:
            continue
        ts = r.get("received_at_utc") or ""
        prev = latest_by_match.get(mid)
        if prev and prev[0] >= ts:
            continue
        latest_by_match[mid] = (ts, _truthy(r.get("game_over")))
    return {mid for mid, (_, go) in latest_by_match.items() if go}


def _latest_raw_snapshots_by_match() -> dict[str, dict]:
    by_match: dict[str, dict] = {}
    min_ts = datetime.now(timezone.utc).timestamp() - _LIVE_GAME_MAX_AGE_SEC
    for r in _read_csv(RAW_SNAPSHOTS_CSV_PATH, tail_lines=5000):
        mid = r.get("match_id") or r.get("lobby_id") or ""
        if not mid:
            continue
        if _truthy(r.get("game_over")):
            by_match.pop(mid, None)
            continue
        ts = _parse_utc_ts(r.get("received_at_utc"))
        if not ts or ts.timestamp() < min_ts:
            continue
        gt_sec = _to_int(r.get("game_time_sec"), 0)
        if gt_sec < 30:
            continue
        prev = by_match.get(mid)
        if prev:
            prev_gt = _to_int(prev.get("game_time_sec"), 0)
            if gt_sec < prev_gt:
                continue
            if gt_sec == prev_gt and (r.get("received_at_utc") or "") <= (prev.get("received_at_utc") or ""):
                continue
        by_match[mid] = r
    return by_match

def _extract_players(r: dict) -> list[dict]:
    players = []
    for side in ("radiant", "dire"):
        team_num = 0 if side == "radiant" else 1
        for p_idx in range(1, 6):
            prefix = f"{side}_p{p_idx}_"
            hid_raw = r.get(f"{prefix}hero_id")
            if hid_raw is None or hid_raw == "":
                continue
            hid = _to_int(hid_raw)
            players.append({
                "account_id": r.get(f"{prefix}account_id"),
                "name": r.get(f"{prefix}player_name") or "Unknown",
                "hero_id": hid,
                "hero_name": HERO_ID_MAP.get(hid, f"Hero {hid}") if hid > 0 else "PICKING...",
                "team": team_num,
                "kills": _to_int(r.get(f"{prefix}kills"), 0),
                "deaths": _to_int(r.get(f"{prefix}deaths"), 0),
                "assists": _to_int(r.get(f"{prefix}assists"), 0),
                "net_worth": _to_int(r.get(f"{prefix}net_worth"), 0),
                "gpm": _to_int(r.get(f"{prefix}gpm"), 0),
                "xpm": _to_int(r.get(f"{prefix}xpm"), 0),
                "level": _to_int(r.get(f"{prefix}level"), 0),
            })
    return players


def _live_games() -> list[dict]:
    llg_by_match = _latest_liveleague_by_match()
    raw_by_match = _latest_raw_snapshots_by_match()
    
    # Strictly only show matches that are actively mapped
    mapped_ids = _get_mapped_match_ids()
    
    # If raw_snapshots' most-recent row for a match has game_over=True, the
    # match is finished even if rich_context is still being updated with stale
    # state (delayed source ticks longer than the actual game). Exclude.
    finished_match_ids = _read_finished_match_ids()
    
    games = []
    for mid in sorted(set(raw_by_match) | set(llg_by_match)):
        if mid not in mapped_ids:
            continue
        if mid in finished_match_ids:
            continue
        raw = raw_by_match.get(mid)
        llg = llg_by_match.get(mid, {})
        r = raw or llg
        data_source = r.get("data_source") or "live_league"
        # Prefer raw_snapshots for tower/networth fields — rich_context often
        # leaves them blank, while raw_snapshots has the GetTopLiveGame state.
        # Pick from whichever source has the value, raw first.
        def _pick(*keys):
            for src in (raw, llg):
                if not src:
                    continue
                for k in keys:
                    v = src.get(k)
                    if v not in (None, ""):
                        return v
            return None

        r_net = _pick("radiant_net_worth")
        d_net = _pick("dire_net_worth")
        rn = _to_int(r_net, 0) if r_net is not None else 0
        dn = _to_int(d_net, 0) if d_net is not None else 0
        nw_diff = rn - dn
        rad_lead = _pick("radiant_lead")
        if rad_lead is not None:
            nw_diff = _to_int(rad_lead, nw_diff)

        # tower_state from GetTopLiveGame is a single 22-bit int: bits 0-10 =
        # radiant (T1T..T4B), bits 11-21 = dire. _towers_alive on the combined
        # value would count BOTH sides — split it first.
        rad_t = _pick("radiant_tower_state")
        dire_t = _pick("dire_tower_state")
        if rad_t is None and dire_t is None:
            combined = _pick("tower_state", "building_state")
            try:
                bits = int(float(combined)) if combined is not None else 0
            except (TypeError, ValueError):
                bits = 0
            SIDE_MASK = 0x7FF
            rad_t = bits & SIDE_MASK
            dire_t = (bits >> 11) & SIDE_MASK
        r_towers = _towers_alive(rad_t if rad_t is not None else "0")
        d_towers = _towers_alive(dire_t if dire_t is not None else "0")
        
        # Priority 1: Rich Context (Realtime Stats)
        players = []
        if llg:
            players = _extract_players(llg)
            
        # Priority 2: Raw TopLive (Limited but better than nothing)
        if not players and raw and "players" in raw:
            # Note: steam_client already normalized these
            raw_players = raw.get("players")
            if isinstance(raw_players, list):
                players = raw_players
            elif isinstance(raw_players, str):
                try: players = json.loads(raw_players)
                except: pass

        games.append({
            "match_id": mid,
            "data_source": data_source,
            "radiant_team": r.get("radiant_team") or llg.get("radiant_team") or "Radiant",
            "dire_team": r.get("dire_team") or llg.get("dire_team") or "Dire",
            "game_time_sec": r.get("game_time_sec", ""),
            "radiant_score": r.get("radiant_score", "0"),
            "dire_score": r.get("dire_score", "0"),
            "net_worth_diff": nw_diff,
            "radiant_towers": r_towers,
            "dire_towers": d_towers,
            "timestamp_utc": r.get("received_at_utc") or r.get("timestamp_utc", ""),
            "players": players,
        })
    games.sort(key=lambda g: g.get("timestamp_utc", ""), reverse=True)
    return games

# ---------------------------------------------------------------------------
# WebSocket & Broadcast
# ---------------------------------------------------------------------------

_ws_clients: set[web.WebSocketResponse] = set()

async def _api_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    _ws_clients.add(ws)
    try:
        async for msg in ws:
            pass  # We only push data, no need to handle incoming WS messages yet
    finally:
        _ws_clients.discard(ws)
    return ws


async def _broadcast_loop(app: web.Application) -> None:
    """Push state to all connected WS clients every second."""
    import asyncio
    while True:
        if _ws_clients:
            try:
                # Re-use the existing data aggregation logic
                trades  = _read_csv(PAPER_TRADES_CSV_PATH)
                signal_rows = _read_csv(CSV_LOG_PATH, tail_lines=500)
                event_rows = _read_csv(DOTA_EVENTS_CSV_PATH, tail_lines=500)
                book_rows = _read_csv(BOOK_EVENTS_CSV_PATH, tail_lines=1000)
                raw_rows = _read_csv(RAW_SNAPSHOTS_CSV_PATH, tail_lines=5000)
                signals = list(reversed(signal_rows[-_FEED_ROWS:]))
                events  = list(reversed(event_rows[-_FEED_ROWS:]))
                prices  = _latest_prices()
                rescue = list(reversed(_read_csv(BOOK_REFRESH_RESCUE_CSV_PATH, tail_lines=500)[-_FEED_ROWS:]))
                match_winner = list(reversed(_read_csv(MATCH_WINNER_CSV_PATH, tail_lines=500)[-_FEED_ROWS:]))
                games = _live_games()

                attempts = _read_csv(LIVE_ATTEMPTS_CSV_PATH)
                from storage_v2 import StorageV2
                storage = StorageV2()
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                from live_state import live_state_mode
                live_state = storage.load_daily_budget(today, mode=live_state_mode()) or {}
                live_positions = storage.load_positions(mode="live")
                
                valid, _ = load_valid_mappings()
                mapped_markets = {}
                for m in valid:
                    mid = str(m.get("dota_match_id") or "")
                    if mid and not mid.startswith("STEAM_"):
                        if mid not in mapped_markets:
                            mapped_markets[mid] = []
                        mapped_markets[mid].append({
                            "name": m.get("name"),
                            "yes_token_id": m.get("yes_token_id"),
                            "no_token_id": m.get("no_token_id"),
                            "yes_team": m.get("yes_team"),
                            "no_team": m.get("no_team"),
                            "market_type": m.get("market_type"),
                        })

                payload = {
                    "type":             "update",
                    "ts":               datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "stats":            _session_data(trades),
                    "health":           _data_health(raw_rows, signal_rows, event_rows, book_rows, games),
                    "live":             await _live_health(),
                    "open_positions":   _open_positions(trades),
                    "closed_positions": _closed_positions(trades, _EXIT_ROWS),
                    "signals":          signals,
                    "events":           events,
                    "prices":           prices,
                    "rescue":           rescue,
                    "match_winner":     match_winner,
                    "games":            games,
                    "attempts":         list(reversed(attempts[-_FEED_ROWS:])),
                    "signal_decisions": [
                        {
                            "timestamp_utc": r.get("timestamp_utc", ""),
                            "event_type":    r.get("event_type", ""),
                            "decision":      r.get("decision", ""),
                            "skip_reason":   r.get("skip_reason", ""),
                            "market_name":   (r.get("market_name", "") or "")[:38],
                            "match_id":      r.get("match_id", ""),
                            "ask":           r.get("ask", ""),
                            "spread":        r.get("spread", ""),
                        }
                        for r in list(reversed(signal_rows[-_FEED_ROWS:]))
                    ],
                    "live_positions":   live_positions,
                    "live_state":       live_state,
                    "mapped_markets":   mapped_markets,
                }
                
                payload_str = json.dumps(payload, default=str)
                for ws in list(_ws_clients):
                    try:
                        await ws.send_str(payload_str)
                    except Exception:
                        _ws_clients.discard(ws)
            except Exception as e:
                print(f"WS Broadcast Error: {e}")
                
        await asyncio.sleep(1.0)


async def _start_background_tasks(app: web.Application) -> None:
    import asyncio
    app['broadcast_task'] = asyncio.create_task(_broadcast_loop(app))


async def _cleanup_background_tasks(app: web.Application) -> None:
    app['broadcast_task'].cancel()
    await app['broadcast_task']


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------

async def _api_data(_request: web.Request) -> web.Response:
    print(f"API Request: {datetime.now(timezone.utc).isoformat()}")
    trades  = _read_csv(PAPER_TRADES_CSV_PATH)
    signal_rows = _read_csv(CSV_LOG_PATH, tail_lines=500)
    event_rows = _read_csv(DOTA_EVENTS_CSV_PATH, tail_lines=500)
    book_rows = _read_csv(BOOK_EVENTS_CSV_PATH, tail_lines=1000)
    raw_rows = _read_csv(RAW_SNAPSHOTS_CSV_PATH, tail_lines=5000)
    
    signals = list(reversed(signal_rows[-_FEED_ROWS:]))
    events  = list(reversed(event_rows[-_FEED_ROWS:]))
    prices  = _latest_prices()
    rescue = list(reversed(_read_csv(BOOK_REFRESH_RESCUE_CSV_PATH, tail_lines=500)[-_FEED_ROWS:]))
    match_winner = list(reversed(_read_csv(MATCH_WINNER_CSV_PATH, tail_lines=500)[-_FEED_ROWS:]))
    games = _live_games()

    print(f"  GAMES: {len(games)}  SIGNALS: {len(signals)}  PRICES: {len(prices)}")

    attempts = _read_csv(LIVE_ATTEMPTS_CSV_PATH, tail_lines=1000)
    from storage_v2 import StorageV2
    storage = StorageV2()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    from live_state import live_state_mode
    live_state = storage.load_daily_budget(today, mode=live_state_mode()) or {}
    live_positions = storage.load_positions(mode="live")
    
    valid, _ = load_valid_mappings()
    mapped_markets = {}
    for m in valid:
        mid = str(m.get("dota_match_id") or "")
        if mid and not mid.startswith("STEAM_"):
            if mid not in mapped_markets:
                mapped_markets[mid] = []
            mapped_markets[mid].append({
                "name": m.get("name"),
                "yes_token_id": m.get("yes_token_id"),
                "no_token_id": m.get("no_token_id"),
                "yes_team": m.get("yes_team"),
                "no_team": m.get("no_team"),
                "market_type": m.get("market_type"),
            })

    print(f"  MAPPED MARKETS: {len(mapped_markets)}")

    health = _data_health(raw_rows, signal_rows, event_rows, book_rows, games)
    try:
        from config import ENABLE_REAL_LIVE_TRADING as _ERLT
        health["real_money"] = bool(_ERLT)
    except Exception:
        health["real_money"] = False
    payload = {
        "ts":               datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stats":            _session_data(trades),
        "health":           health,
        "live":             await _live_health(),
        "open_positions":   _open_positions(trades),
        "closed_positions": _closed_positions(trades, _EXIT_ROWS),
        "signals":          signals,
        "events":           events,
        "prices":           prices,
        "rescue":           rescue,
        "match_winner":     match_winner,
        "games":            games,
        "attempts":         list(reversed(attempts[-_FEED_ROWS:])),
        "signal_decisions": [
            {
                "timestamp_utc": r.get("timestamp_utc", ""),
                "event_type":    r.get("event_type", ""),
                "decision":      r.get("decision", ""),
                "skip_reason":   r.get("skip_reason", ""),
                "market_name":   (r.get("market_name", "") or "")[:38],
                "match_id":      r.get("match_id", ""),
                "ask":           r.get("ask", ""),
                "spread":        r.get("spread", ""),
            }
            for r in list(reversed(signal_rows[-_FEED_ROWS:]))
        ],
        "live_positions":   live_positions,
        "live_state":       live_state,
        "mapped_markets":   mapped_markets,
    }
    return web.Response(
        text=json.dumps(payload, default=str),
        content_type="application/json",
        headers={"Access-Control-Allow-Origin": "*"},
    )



_ASSETS_DIR = Path(__file__).parent / "dashboard_assets"
_INDEX_HTML_PATH = _ASSETS_DIR / "index.html"

async def _index(_request: web.Request) -> web.Response:
    """Serve index.html with cache-busted asset URLs.

    Uses mtime of style.css/app.js as the version so each rebuild forces a
    fresh fetch from the browser without manual hard-refresh. Also sets
    no-cache headers on the HTML itself.
    """
    html = _INDEX_HTML_PATH.read_text()
    css_v = int(Path(_ASSETS_DIR / "style.css").stat().st_mtime)
    js_v = int(Path(_ASSETS_DIR / "app.js").stat().st_mtime)
    html = html.replace('/assets/style.css', f'/assets/style.css?v={css_v}')
    html = html.replace('/assets/app.js', f'/assets/app.js?v={js_v}')
    return web.Response(
        text=html,
        content_type="text/html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )



async def _api_trade(request: web.Request) -> web.Response:
    """Enqueue a manual BUY for the bot's executor to process."""
    from manual_orders import enqueue
    try:
        data = await request.json()
        order = {
            "action": "buy",
            "token_id": str(data.get("token_id") or ""),
            "match_id": str(data.get("match_id") or ""),
            "size_usd": float(data.get("amount_usd") or 0),
            "price_cap": float(data.get("price_cap") or 0),
            "source": "dashboard_manual",
        }
        if not order["token_id"] or order["size_usd"] <= 0:
            return web.json_response({"status": "error", "error": "missing token_id or size"}, status=400)
        oid = enqueue(order)
        print(f"Manual BUY queued: id={oid} token={order['token_id']} ${order['size_usd']} cap={order['price_cap']}")
        return web.json_response({"status": "queued", "id": oid})
    except Exception as e:
        print(f"Error queuing manual trade: {e}")
        return web.json_response({"status": "error", "error": str(e)}, status=500)


async def _api_exit(request: web.Request) -> web.Response:
    """Enqueue a manual EXIT (FAK sell at best bid) for the bot's executor."""
    from manual_orders import enqueue
    try:
        data = await request.json()
        order = {
            "action": "exit",
            "token_id": str(data.get("token_id") or ""),
            "match_id": str(data.get("match_id") or ""),
            "shares": float(data.get("shares") or 0) or None,
            "source": "dashboard_manual",
        }
        if not order["token_id"]:
            return web.json_response({"status": "error", "error": "missing token_id"}, status=400)
        oid = enqueue(order)
        print(f"Manual EXIT queued: id={oid} token={order['token_id']} match={order['match_id']}")
        return web.json_response({"status": "queued", "id": oid})
    except Exception as e:
        print(f"Error queuing manual exit: {e}")
        return web.json_response({"status": "error", "error": str(e)}, status=500)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Paper trading dashboard")
    parser.add_argument("--port", type=int, default=int(os.getenv("DASHBOARD_PORT", "8080")))
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    app = web.Application()
    app.on_startup.append(_start_background_tasks)
    app.on_cleanup.append(_cleanup_background_tasks)
    
    app.router.add_get("/", _index)
    app.router.add_get("/api/data", _api_data)
    app.router.add_get("/api/ws", _api_ws)
    app.router.add_post("/api/trade", _api_trade)
    app.router.add_post("/api/exit", _api_exit)

    workspace_dir = os.path.dirname(__file__)
    app.router.add_static("/assets", os.path.join(workspace_dir, "dashboard_assets"))
    app.router.add_static("/logs", os.path.join(workspace_dir, "logs"))

    print(f"Dashboard → http://localhost:{args.port}")
    print(f"Terminal  → http://localhost:{args.port}/")
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
