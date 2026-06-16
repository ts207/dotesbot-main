"""
backtest_live_data.py — Replay backtest using today's DreamLeague data.

Sources:
  logs/raw_snapshots.csv  — game-state ticks (1 per Valve update per match)
  logs/book_events.csv    — order book snapshots (15k+ rows across all tokens)
  markets.yaml            — match_id → yes/no token mapping

Usage:
    python3 backtest_live_data.py
    python3 backtest_live_data.py --min-lag 0.05 --min-edge 0.003 --diagnostics
    python3 backtest_live_data.py --sweep
    python3 backtest_live_data.py --csv-out trades.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from event_detector import EventDetector
from signal_engine import ACTIVE_EVENTS, apply_probability_move, _EVENT_MAX_FILL, PRIMARY_TRADE_EVENTS
from config import (
    MAX_SPREAD,
    MAX_VOLATILITY_SPREAD,
    MAX_MOMENTUM_CHASE,
    MIN_ASK_SIZE_USD,
    MIN_EXECUTABLE_EDGE,
    MIN_LAG,
    PAPER_EXECUTION_DELAY_MS,
    PAPER_SLIPPAGE_CENTS,
    PRICE_LOOKBACK_SEC,
    TRADE_EVENTS,
    EXIT_STOP_LOSS_ABS,
    EXIT_STOP_LOSS_REL,
    EXIT_HORIZON_SEC,
    EXIT_HORIZON_BY_EVENT,
    MAX_OPEN_POSITIONS,
    MAX_TOTAL_LIVE_USD,
    FIGHT_SWING_MIN_GAME_TIME_SEC,
    FIGHT_SWING_MAX_GAME_TIME_SEC,
    EXIT_TRAILING_STOP_CENTS,
    EXIT_TRAILING_STOP_GRACE_SEC,
)

# Realistic time from Steam receipt to local decision
DETECTION_LATENCY_MS = 400

# ---------------------------------------------------------------------------
# Defaults / paths
# ---------------------------------------------------------------------------
SNAPSHOTS_PATH  = Path("logs/raw_snapshots.csv")
BOOK_PATH       = Path("logs/book_events.csv")
MARKETS_YAML    = Path("markets.yaml")
PAPER_SIZE_USD  = 5.0   # match live MAX_TRADE_USD

# All PnL horizons used throughout — order matters (ascending).
_HORIZONS: list[tuple[int, str]] = [
    (5,   "pnl_5s"),
    (10,  "pnl_10s"),
    (15,  "pnl_15s"),
    (20,  "pnl_20s"),
    (30,  "pnl_30s"),
    (45,  "pnl_45s"),
    (60,  "pnl_60s"),
    (90,  "pnl_90s"),
    (120, "pnl_120s"),
]

# Active event types (uses ACTIVE_EVENTS from signal_engine)
_ACTIVE = set(ACTIVE_EVENTS)
# Default trade events — mirrors .env TRADE_EVENTS (excludes disabled signals)
_TRADE_DEFAULT = TRADE_EVENTS & _ACTIVE

# ---------------------------------------------------------------------------
# Team-name helpers
# ---------------------------------------------------------------------------

def _normalise_team(name: str) -> str:
    """Lower-case, strip whitespace and common suffix noise."""
    return name.strip().lower()


def _teams_match(a: str, b: str) -> bool:
    """True if two team names refer to the same team (handles truncation like
    'Aurora Gaming' vs 'Aurora')."""
    a, b = _normalise_team(a), _normalise_team(b)
    return a == b or a.startswith(b) or b.startswith(a)


def _load_radiant_teams_from_jsonl(wanted: set[str]) -> dict[str, str]:
    """Scan liveleague_raw.jsonl for {match_id: radiant_team_name}, stopping early
    once all wanted match_ids have been found.

    NOTE: Each JSONL line is a full liveleague payload (can be MB-sized). If the
    file exceeds 200 MB we skip the scan to avoid multi-minute hangs — the CSV
    fallback covers the vast majority of matches already.
    """
    result: dict[str, str] = {}
    if not wanted:
        return result
    p = Path("logs/liveleague_raw.jsonl")
    if not p.exists():
        return result
    if p.stat().st_size > 200 * 1024 * 1024:  # >200 MB → too slow to parse
        return result
    import json
    remaining = set(wanted)
    with p.open(encoding="utf-8") as f:
        for line in f:
            if not remaining:
                break
            try:
                obj = json.loads(line)
            except Exception:
                continue
            mid = str(obj.get("match_id", "")).strip()
            rad = str(obj.get("radiant_team", "")).strip()
            if mid and rad and mid in remaining:
                result[mid] = rad
                remaining.discard(mid)
    return result


def _load_yes_is_radiant() -> dict[str, bool]:
    """
    Returns {match_id: yes_is_radiant} using (in priority order):
      1. dota_events.csv + signals.csv  (most reliable, already processed)
      2. liveleague_raw.jsonl + markets.yaml yes_team  (fills gaps)
    """
    result: dict[str, bool] = {}
    for fname in ("logs/dota_events.csv", "logs/signals.csv"):
        p = Path(fname)
        if not p.exists():
            continue
        with p.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                mid = row.get("match_id", "").strip()
                rad = row.get("radiant_team", "").strip()
                yes = row.get("yes_team", "").strip()
                if mid and rad and yes and mid not in result:
                    result[mid] = _teams_match(rad, yes)

    # Fill remaining gaps from liveleague_raw.jsonl + markets.yaml
    missing = set()
    with MARKETS_YAML.open(encoding="utf-8") as f:
        import yaml as _yaml
        data = _yaml.safe_load(f)
    markets = data.get("markets", data) if isinstance(data, dict) else data
    yes_teams = {str(m.get("dota_match_id", "")).strip(): str(m.get("yes_team", "")).strip()
                 for m in markets}

    for mid in yes_teams:
        if mid and mid not in result:
            missing.add(mid)

    if missing:
        radiant_from_jsonl = _load_radiant_teams_from_jsonl(missing)
        for mid in missing:
            rad = radiant_from_jsonl.get(mid, "")
            yes = yes_teams.get(mid, "")
            if rad and yes:
                result[mid] = _teams_match(rad, yes)

    return result


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_snapshots() -> dict[str, list[dict]]:
    """Returns {match_id: [sorted, deduped game-state snaps]} from raw_snapshots.csv."""
    by_match: dict[str, list] = defaultdict(list)
    with SNAPSHOTS_PATH.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            mid = row.get("match_id", "").strip()
            ns  = int(row.get("received_at_ns") or 0)
            gt  = int(float(row.get("game_time_sec") or 0))
            if not mid or ns == 0:
                continue
            by_match[mid].append({
                "ts_ns":         ns,
                "ts_ms":         ns // 1_000_000,
                "game_time_sec": gt,
                "radiant_lead":  int(float(row.get("radiant_lead") or 0)),
                "radiant_score": int(float(row.get("radiant_score") or 0)),
                "dire_score":    int(float(row.get("dire_score") or 0)),
                "tower_state":   int(float(row["tower_state"])) if row.get("tower_state") else None,
                "building_state":int(float(row["building_state"])) if row.get("building_state") else None,
                "roshan_respawn_timer": int(float(row["roshan_respawn_timer"])) if row.get("roshan_respawn_timer") else 0,
                "match_id":      mid,
            })
    result = {}
    for mid, snaps in by_match.items():
        seen_gt: set[int] = set()
        deduped = []
        for s in sorted(snaps, key=lambda x: x["ts_ns"]):
            if s["game_time_sec"] not in seen_gt:
                seen_gt.add(s["game_time_sec"])
                deduped.append(s)
        result[mid] = deduped
    return result


def _load_book() -> dict[str, list[dict]]:
    """Returns {token_id: [sorted book ticks]}. Only keeps rows with best_ask."""
    by_token: dict[str, list] = defaultdict(list)
    with BOOK_PATH.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tok = row.get("asset_id", "").strip()
            ts_str = row.get("timestamp_utc", "")
            ask = row.get("best_ask", "")
            bid = row.get("best_bid", "")
            if not tok or not ask:
                continue
            # Parse ISO timestamp to ms
            from datetime import datetime, timezone
            try:
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                ts_ms = int(dt.timestamp() * 1000)
            except Exception:
                continue
            by_token[tok].append({
                "ts_ms":    ts_ms,
                "best_ask": float(ask) if ask else None,
                "best_bid": float(bid) if bid else None,
                "mid":      float(row["mid"]) if row.get("mid") else None,
                "spread":   float(row["spread"]) if row.get("spread") else None,
                "ask_size": float(row["ask_size"]) if row.get("ask_size") else None,
            })
    return {tok: sorted(ticks, key=lambda x: x["ts_ms"]) for tok, ticks in by_token.items()}


def _load_markets() -> dict[str, dict]:
    """Returns {match_id: market_dict} for all confidence=1.0 mappings with valid tokens."""
    with MARKETS_YAML.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    markets = data.get("markets", data) if isinstance(data, dict) else data
    result = {}
    for m in markets:
        mid  = str(m.get("dota_match_id", "")).strip()
        conf = float(m.get("confidence", 0))
        yes  = str(m.get("yes_token_id", "")).strip()
        no   = str(m.get("no_token_id", "")).strip()
        if (conf < 1.0 or not mid or not yes or not no
                or mid in ("PLACEHOLDER", "STEAM_MATCH_OR_LOBBY_ID_HERE", "0")
                or yes in ("PLACEHOLDER", "")
                or no in ("PLACEHOLDER", "")):
            continue
        result[mid] = {
            "match_id":     mid,
            "name":         m.get("name", ""),
            "yes_token_id": yes,
            "no_token_id":  no,
            "yes_team":     m.get("yes_team", ""),
            "no_team":      m.get("no_team", ""),
            "tick_size":    str(m.get("tick_size", "0.01")),
            "neg_risk":     bool(m.get("neg_risk", False)),
        }
    return result

# ---------------------------------------------------------------------------
# Binary search helpers
# ---------------------------------------------------------------------------

def _nearest_before(ticks: list[dict], ts_ms: int) -> dict | None:
    lo, hi, result = 0, len(ticks) - 1, None
    while lo <= hi:
        m = (lo + hi) // 2
        if ticks[m]["ts_ms"] <= ts_ms:
            result = ticks[m]; lo = m + 1
        else:
            hi = m - 1
    return result


def _price_at(ticks: list[dict], ts_ms: int, field: str) -> float | None:
    t = _nearest_before(ticks, ts_ms)
    if t is None:
        return None
    v = t.get(field)
    return float(v) if v is not None else None


def load_data() -> dict:
    """Load all replay data once; pass as `_data=` to run_backtest for sweep caching."""
    return {
        "snapshots":     _load_snapshots(),
        "book":          _load_book(),
        "markets":       _load_markets(),
        "yes_is_radiant": _load_yes_is_radiant(),
    }

# ---------------------------------------------------------------------------
# Trade dataclass
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    match_name:      str
    match_id:        str
    event_type:      str
    direction:       str        # "radiant" or "dire"
    side:            str        # "YES" or "NO"
    game_time_sec:   int
    entry_ts_ms:     int
    fill_price:      float
    fair_price:      float
    lag:             float
    edge:            float
    spread:          float | None
    pnl_5s:          float | None = None
    pnl_10s:         float | None = None
    pnl_15s:         float | None = None
    pnl_20s:         float | None = None
    pnl_30s:         float | None = None
    pnl_45s:         float | None = None
    pnl_60s:         float | None = None
    pnl_90s:         float | None = None
    pnl_120s:        float | None = None
    pnl_settle:      float | None = None   # P&L at game end (last book tick)
    skip_reason:     str | None = None     # None = traded
    stop_loss_exit:  bool = False
    peak_bid:        float = 0.0           # highest bid seen since entry (trailing stop tracking)

# ---------------------------------------------------------------------------
# Main backtest
# ---------------------------------------------------------------------------

def run_backtest(
    *,
    min_lag:        float = 0.05,
    min_edge:       float = MIN_EXECUTABLE_EDGE,
    max_spread:     float = MAX_SPREAD,
    min_ask_usd:    float = MIN_ASK_SIZE_USD,
    slippage:       float = PAPER_SLIPPAGE_CENTS,
    lookback_sec:   float = PRICE_LOOKBACK_SEC,
    size_usd:       float = PAPER_SIZE_USD,
    exit_sec:       int   = 30,
    exit_at_mid:    bool  = False,  # True = Maker exit, False = Taker exit
    entry_at_mid:   bool  = False,  # True = Maker entry
    trade_events:   set[str] | None = None,
    fill_caps:      dict[str, float] | None = None,
    diagnostics:    Counter | None = None,
    stop_loss_rel:    float | None = EXIT_STOP_LOSS_REL,
    stop_loss_abs:    float | None = EXIT_STOP_LOSS_ABS,
    max_positions:    int | None = MAX_OPEN_POSITIONS,
    max_total_usd:    float | None = MAX_TOTAL_LIVE_USD,
    min_game_time_sec: int = 0,           # skip events before this game time (e.g. 1800 = 30 min)
    max_game_time_sec: int = 0,           # skip events after this game time (0 = unlimited)
    reentry_cooldown_sec: int = 300,      # block same direction+event re-entry within this window (matches paper_trader)
    settle_edge:      float | None = None, # trades with edge >= this get pnl_settle computed
    max_exit_stale_ms: int | None = None,  # if exit tick is older than this, record PnL as None
    max_book_age_ms:   int = 15000,         # entry book staleness gate (ms); default matches live bot
    _data:            dict | None = None,
) -> tuple[list[Trade], int]:

    allowed = trade_events if trade_events is not None else _TRADE_DEFAULT
    caps    = fill_caps if fill_caps is not None else _EVENT_MAX_FILL
    exit_field = "mid" if exit_at_mid else "best_bid"
    entry_field = "mid" if entry_at_mid else "best_ask"

    def diag(key: str) -> None:
        if diagnostics is not None:
            diagnostics[key] += 1

    if _data is None:
        _data = load_data()
    snapshots          = _data["snapshots"]
    book               = _data["book"]
    markets            = _data["markets"]
    yes_is_radiant_map = _data["yes_is_radiant"]

    # Only process matches that appear in both snapshots and markets
    valid_matches = set(snapshots) & set(markets)
    n_evaluated = 0

    trades: list[Trade] = []

    for mid in sorted(valid_matches):
        mkt   = markets[mid]
        snaps = snapshots[mid]
        yes_tok = mkt["yes_token_id"]
        no_tok  = mkt["no_token_id"]
        yes_ticks = book.get(yes_tok, [])
        no_ticks  = book.get(no_tok, [])

        if not yes_ticks and not no_ticks:
            diag("match_no_book_data")
            continue

        # Determine whether the YES token belongs to the radiant team.
        # Event direction "radiant" means radiant is doing well → buy radiant_team's token.
        # If yes_team == radiant_team → that token is YES; otherwise it's NO.
        yes_is_radiant = yes_is_radiant_map.get(mid)
        if yes_is_radiant is None:
            diag("match_no_team_mapping")
            continue  # skip matches we can't correctly orient

        n_evaluated += 1
        detector = EventDetector()
        cooldown_until_ms: dict[tuple[str, str], int] = {}
        active_trades: list[Trade] = []
        game_end_ts_ms = snaps[-1]["ts_ms"]   # last snapshot ≈ game end

        for snap in snaps:
            ts_ms = snap["ts_ms"]
            gt    = snap["game_time_sec"]
            events = detector.observe(snap)

            # --- EXPIRE CLOSED POSITIONS ---
            # Remove positions whose exit window has elapsed so budget frees up.
            for t in list(active_trades):
                if ts_ms >= t.entry_ts_ms + exit_sec * 1000:
                    active_trades.remove(t)

            # --- STOP-LOSS + TRAILING STOP CHECK ---
            if active_trades:
                for t in list(active_trades):
                    ticks = yes_ticks if t.side == "YES" else no_ticks
                    cur_bid = _price_at(ticks, ts_ms, "best_bid")
                    if cur_bid is None:
                        continue
                    # Update peak bid
                    if cur_bid > t.peak_bid:
                        t.peak_bid = cur_bid
                    age_sec = (ts_ms - t.entry_ts_ms) / 1000.0
                    triggered = False
                    reason = ""
                    # Fixed stop-loss
                    if stop_loss_rel is not None or stop_loss_abs is not None:
                        stop_price = t.fill_price - (stop_loss_rel or 1.0)
                        if stop_loss_abs is not None:
                            stop_price = max(stop_price, stop_loss_abs)
                        if cur_bid <= stop_price:
                            triggered = True; reason = "stop_loss"
                    # Trailing stop: peak dropped by threshold after grace period
                    if (not triggered and EXIT_TRAILING_STOP_CENTS > 0
                            and age_sec >= EXIT_TRAILING_STOP_GRACE_SEC
                            and t.peak_bid > t.fill_price
                            and cur_bid <= t.peak_bid - EXIT_TRAILING_STOP_CENTS):
                        triggered = True; reason = "trailing_stop"
                    if triggered:
                        pnl_exit = round((cur_bid - t.fill_price) * size_usd, 4)
                        for horizon_sec, attr in _HORIZONS:
                            if ts_ms < t.entry_ts_ms + horizon_sec * 1000:
                                setattr(t, attr, pnl_exit)
                        t.stop_loss_exit = True
                        active_trades.remove(t)
                        diag(reason)

            # --- CONTRARY-EVENT EXIT CHECK ---
            # Mirrors live bot: when a primary event fires for the OTHER team, exit now.
            # Pre-computed horizon P&Ls for future horizons are replaced with the actual
            # exit price at the contrary event time (past horizons keep their real values).
            if events and active_trades:
                for evt in events:
                    if evt.event_type not in PRIMARY_TRADE_EVENTS:
                        continue  # only primary events trigger contrary exit (mirrors live bot)
                    if not evt.direction:
                        continue
                    for t in list(active_trades):
                        if evt.direction == t.direction:
                            continue  # same direction — not contrary
                        ticks = yes_ticks if t.side == "YES" else no_ticks
                        exit_px = _price_at(ticks, ts_ms, exit_field)
                        if exit_px is not None:
                            pnl_early = round((exit_px - t.fill_price) * size_usd, 4)
                            for horizon_sec, attr in _HORIZONS:
                                if ts_ms < t.entry_ts_ms + horizon_sec * 1000:
                                    setattr(t, attr, pnl_early)  # override future horizons
                            t.pnl_settle = pnl_early  # we exited; don't hold to settlement
                            active_trades.remove(t)
                            diag(f"contrary_exit:{evt.event_type}")

            for evt in events:
                diag(f"event_seen:{evt.event_type}")

                if min_game_time_sec and gt < min_game_time_sec:
                    diag("reject:before_min_game_time"); continue
                if max_game_time_sec and gt > max_game_time_sec:
                    diag("reject:after_max_game_time"); continue
                # FIGHT_SWING-specific game time window (mirrors signal_engine)
                if evt.event_type == "POLL_FIGHT_SWING":
                    if FIGHT_SWING_MIN_GAME_TIME_SEC and gt < FIGHT_SWING_MIN_GAME_TIME_SEC:
                        diag("reject:fight_swing_too_early"); continue
                    if FIGHT_SWING_MAX_GAME_TIME_SEC and gt > FIGHT_SWING_MAX_GAME_TIME_SEC:
                        diag("reject:fight_swing_too_late"); continue

                if evt.event_type not in _ACTIVE:
                    diag("reject:inactive_event"); continue
                if evt.event_type not in allowed:
                    diag("reject:not_in_trade_events"); continue

                direction = evt.direction
                if direction not in ("radiant", "dire"):
                    diag("reject:direction_unknown"); continue

                ck = (direction, evt.event_type)
                cooldown_window_ms = max(exit_sec * 1000, reentry_cooldown_sec * 1000)
                if ts_ms < cooldown_until_ms.get(ck, 0):
                    diag("reject:cooldown"); continue

                # Correct direction → token mapping using per-match team orientation.
                # buy_yes: True when the benefiting team's token is the YES token.
                #   radiant event + yes_is_radiant  → buy YES (radiant = yes_team)
                #   radiant event + not yes_is_radiant → buy NO  (radiant = no_team)
                #   dire event + not yes_is_radiant → buy YES (dire = yes_team)
                #   dire event + yes_is_radiant  → buy NO  (dire = no_team)
                buy_yes = (direction == "radiant") == yes_is_radiant
                token_ticks = yes_ticks if buy_yes else no_ticks
                side = "YES" if buy_yes else "NO"

                if not token_ticks:
                    diag("reject:no_book_for_token"); continue

                # Realistic fill time: Steam receipt + detection + execution delay
                fill_ts_ms = ts_ms + DETECTION_LATENCY_MS + PAPER_EXECUTION_DELAY_MS
                event_tick = _nearest_before(token_ticks, fill_ts_ms)
                if not event_tick or event_tick.get(entry_field) is None:
                    diag(f"reject:missing_{entry_field}"); continue

                # Book staleness guard (match live bot)
                book_age_ms = fill_ts_ms - event_tick["ts_ms"]
                if book_age_ms > max_book_age_ms:
                    diag("reject:book_stale"); continue

                ask    = float(event_tick[entry_field])
                bid    = event_tick.get("best_bid")
                spread = event_tick.get("spread")
                if spread is None and bid is not None:
                    spread = ask - float(bid)

                # Spread checks
                if spread is not None and float(spread) > MAX_VOLATILITY_SPREAD:
                    diag("reject:volatility_spread_too_wide"); continue
                if spread is not None and float(spread) > max_spread:
                    diag("reject:spread_too_wide"); continue

                # Liquidity check
                ask_size = event_tick.get("ask_size")
                if ask_size is not None and float(ask_size) * ask < min_ask_usd:
                    diag("reject:insufficient_ask_size"); continue

                # Fill price cap
                cap = caps.get(evt.event_type, 0.80)
                if ask > cap:
                    diag("reject:fill_price_too_high"); continue

                # Anchor price (lookback)
                lookback_ms = int(lookback_sec * 1000)
                anchor_tick = _nearest_before(token_ticks, ts_ms - lookback_ms)
                anchor_price = float(anchor_tick["mid"]) if anchor_tick and anchor_tick.get("mid") else ask

                # Expected move from signal_engine spec
                spec = ACTIVE_EVENTS[evt.event_type]
                expected_move = spec.base
                
                # --- LEAD SCALING ---
                team_lead = snap.get("radiant_lead")
                if team_lead is not None:
                    try:
                        team_lead = int(team_lead)
                        if direction == "dire":
                            team_lead = -team_lead
                        
                        lead_mult = min(1.2, 0.8 + (max(0, team_lead) / 25000.0))
                        if team_lead < -5000:
                            lead_mult = max(0.2, 0.8 + (team_lead / 10000.0))
                        expected_move *= lead_mult
                    except (TypeError, ValueError):
                        pass

                fair = apply_probability_move(anchor_price, expected_move)
                lag  = fair - ask  # how much market still needs to move
                
                # --- MOMENTUM FILTER ---
                market_move = ask - anchor_price
                if expected_move > 0 and market_move / expected_move > MAX_MOMENTUM_CHASE:
                    diag("reject:momentum_exhausted"); continue

                if lag < min_lag:
                    diag("reject:lag_too_small"); continue

                executable_price = min(ask + slippage, 0.99)
                edge = fair - executable_price

                if edge < min_edge:
                    diag("reject:edge_too_small"); continue

                # Budget / position-count constraints (mirrors live bot)
                if max_positions is not None and len(active_trades) >= max_positions:
                    diag("reject:max_positions_reached"); continue
                current_exposure = len(active_trades) * size_usd
                if max_total_usd is not None and current_exposure + size_usd > max_total_usd:
                    diag("reject:max_total_usd_reached"); continue

                # --- TRADE ACCEPTED ---
                diag(f"accepted:{evt.event_type}")

                entry_bid = event_tick.get("best_bid")
                t = Trade(
                    match_name=mkt["name"],
                    match_id=mid,
                    event_type=evt.event_type,
                    direction=direction,
                    side=side,
                    game_time_sec=gt,
                    entry_ts_ms=ts_ms,
                    fill_price=ask,
                    fair_price=round(fair, 4),
                    lag=round(lag, 4),
                    edge=round(edge, 4),
                    spread=round(float(spread), 4) if spread is not None else None,
                    peak_bid=float(entry_bid) if entry_bid is not None else ask,
                )

                # Mark-to-market at all horizons using bid or mid
                for horizon_sec, attr in _HORIZONS:
                    target_ms = ts_ms + horizon_sec * 1000
                    exit_tick_raw = _nearest_before(token_ticks, target_ms)
                    if exit_tick_raw is None:
                        continue
                    if max_exit_stale_ms is not None:
                        tick_age = target_ms - exit_tick_raw["ts_ms"]
                        if tick_age > max_exit_stale_ms:
                            continue  # stale — don't record, avoids bid-ask spread artifact
                    exit_px = exit_tick_raw.get(exit_field)
                    if exit_px is not None:
                        setattr(t, attr, round((float(exit_px) - ask) * size_usd, 4))

                # Settlement P&L: price at game end (computed for all trades, or only high-edge ones)
                if settle_edge is None or edge >= settle_edge:
                    settle_px = _price_at(token_ticks, game_end_ts_ms, exit_field)
                    if settle_px is not None:
                        t.pnl_settle = round((settle_px - ask) * size_usd, 4)

                trades.append(t)
                active_trades.append(t)
                cooldown_until_ms[ck] = ts_ms + cooldown_window_ms

    return trades, n_evaluated


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _fmt(v: float | None) -> str:
    return f"{v:+7.3f}" if v is not None else "    n/a"


def _summary(vals: list[float], size: float) -> str:
    if not vals:
        return "n/a"
    wins  = sum(1 for v in vals if v > 0)
    gross_w = sum(v for v in vals if v > 0)
    gross_l = abs(sum(v for v in vals if v < 0))
    pf_str = f"{gross_w/gross_l:.2f}" if gross_l > 0 else "∞"
    return (f"n={len(vals)}  avg={sum(vals)/len(vals):+.3f}  "
            f"total={sum(vals):+.2f}  wins={wins}/{len(vals)}  "
            f"win%={wins/len(vals):.0%}  PF={pf_str}")


def _max_drawdown(trades: list[Trade], attr: str) -> float | None:
    """Max peak-to-trough on cumulative PnL series, sorted by entry time."""
    vals = [(t.entry_ts_ms, getattr(t, attr)) for t in trades if getattr(t, attr) is not None]
    if not vals:
        return None
    vals.sort()
    peak = cum = 0.0
    max_dd = 0.0
    for _, pnl in vals:
        cum += pnl
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return max_dd


def print_results(trades: list[Trade], *, min_lag: float, min_edge: float,
                  max_spread: float, size_usd: float, exit_sec: int,
                  n_evaluated: int = 0, csv_out: str | None = None) -> None:
    n_traded = len(set(t.match_id for t in trades))
    n_stopped = sum(1 for t in trades if t.stop_loss_exit)
    print(f"\n{'='*72}")
    print(f"  BACKTEST RESULTS  (DreamLeague data — today)")
    print(f"  min_lag={min_lag}  min_edge={min_edge}  max_spread={max_spread}")
    print(f"  size=${size_usd}  exit_horizon={exit_sec}s  "
          f"matches_evaluated={n_evaluated}  matches_with_trades={n_traded}")
    print(f"{'='*72}")

    if not trades:
        print("  No signals fired. Try lowering --min-lag or --min-edge.")
        return

    print(f"\n  Total trades: {len(trades)}  (stop-loss exits: {n_stopped})")
    for horizon_sec, attr in _HORIZONS:
        label = f"{horizon_sec}s"
        vals  = [getattr(t, attr) for t in trades if getattr(t, attr) is not None]
        n_missing = len(trades) - len(vals)
        coverage = f"  cov={len(vals)}/{len(trades)}" if n_missing > 0 else ""
        dd    = _max_drawdown(trades, attr)
        dd_s  = f"  maxDD={dd:+.2f}" if dd is not None else ""
        print(f"  {label:>4}:  {_summary(vals, size_usd)}{dd_s}{coverage}")
    settle_vals = [t.pnl_settle for t in trades if t.pnl_settle is not None]
    if settle_vals:
        dd_s = f"  maxDD={_max_drawdown(trades, 'pnl_settle'):+.2f}"
        print(f"  {'SETT':>4}:  {_summary(settle_vals, size_usd)}{dd_s}")

    # Mixed P&L: uses EXIT_HORIZON_BY_EVENT (or settlement if 0)
    mixed_vals = []
    for t in trades:
        horizon = EXIT_HORIZON_BY_EVENT.get(t.event_type, EXIT_HORIZON_SEC)
        if horizon == 0:
            val = t.pnl_settle
        else:
            attr = f"pnl_{horizon}s"
            val = getattr(t, attr, None)
        if val is not None:
            mixed_vals.append(val)
    
    if mixed_vals:
        print(f"  {'MIXD':>4}:  {_summary(mixed_vals, size_usd)}")

    # Per-event-type — all four horizons
    print(f"\n{'--- By Event Type ':{'─'}<60}")
    by_evt: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        by_evt[t.event_type].append(t)

    rows = []
    for et, ts in sorted(by_evt.items()):
        avg_e = sum(t.edge for t in ts) / len(ts)
        avg_l = sum(t.lag for t in ts) / len(ts)
        h: dict[str, tuple] = {}
        for _, attr in _HORIZONS:
            vals = [getattr(t, attr) for t in ts if getattr(t, attr) is not None]
            h[attr] = (sum(vals)/len(vals), sum(1 for v in vals if v > 0)/len(vals), sum(vals)) if vals else (None, None, None)
        rows.append((et, len(ts), avg_e, avg_l, h))

    rows.sort(key=lambda r: (r[4]["pnl_30s"][0] or -999), reverse=True)
    hdr = (f"  {'Event Type':<32} {'N':>4}  {'AvgEdge':>8}  {'AvgLag':>8}  "
           f"{'Avg15s':>8}  {'Avg30s':>8}  {'Avg60s':>8}  {'Avg120s':>8}  {'W30%':>5}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for et, n, avg_e, avg_l, h in rows:
        a15, _,  _   = h["pnl_15s"]
        a30, w30, _  = h["pnl_30s"]
        a60, _,  _   = h["pnl_60s"]
        a120, _, _   = h["pnl_120s"]
        wp30 = f"{w30*100:.0f}%" if w30 is not None else "n/a"
        print(f"  {et:<32} {n:>4}  {avg_e:>8.4f}  {avg_l:>8.4f}  "
              f"{_fmt(a15):>8}  {_fmt(a30):>8}  {_fmt(a60):>8}  {_fmt(a120):>8}  {wp30:>5}")

    # Game-time stratification
    print(f"\n{'--- By Game-Time Bucket ':{'─'}<60}")
    _GT_BUCKETS = [
        ("early  (<20m)",  lambda t: t.game_time_sec < 1200),
        ("mid    (20-40m)", lambda t: 1200 <= t.game_time_sec < 2400),
        ("late   (40m+)",  lambda t: t.game_time_sec >= 2400),
    ]
    hdr_gt = (f"  {'Bucket':<18} {'N':>4}  {'Avg30s':>8}  {'Tot30s':>8}  {'W30%':>5}"
              f"  {'Avg60s':>8}  {'Avg120s':>8}")
    print(hdr_gt)
    print("  " + "-" * (len(hdr_gt) - 2))
    for label_gt, pred in _GT_BUCKETS:
        bucket = [t for t in trades if pred(t)]
        p30  = [t.pnl_30s  for t in bucket if t.pnl_30s  is not None]
        p60  = [t.pnl_60s  for t in bucket if t.pnl_60s  is not None]
        p120 = [t.pnl_120s for t in bucket if t.pnl_120s is not None]
        w30  = sum(1 for v in p30 if v > 0)
        a30  = sum(p30)/len(p30)   if p30  else None
        a60  = sum(p60)/len(p60)   if p60  else None
        a120 = sum(p120)/len(p120) if p120 else None
        wp30 = f"{w30/len(p30)*100:.0f}%" if p30 else "n/a"
        print(f"  {label_gt:<18} {len(bucket):>4}  {_fmt(a30):>8}  {_fmt(sum(p30) if p30 else None):>8}"
              f"  {wp30:>5}  {_fmt(a60):>8}  {_fmt(a120):>8}")

    # Per-match
    print(f"\n{'--- By Match ':{'─'}<60}")
    by_match: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        by_match[t.match_name].append(t)
    for name, ts in sorted(by_match.items(), key=lambda kv: kv[0]):
        p30  = [t.pnl_30s for t in ts if t.pnl_30s is not None]
        wins = sum(1 for v in p30 if v > 0)
        tot  = sum(p30) if p30 else None
        sl   = sum(1 for t in ts if t.stop_loss_exit)
        sl_s = f"  sl={sl}" if sl else ""
        print(f"  {name[:55]:<55}  n={len(ts):>3}  "
              f"30s_total={_fmt(tot)}  wins={wins}/{len(p30) if p30 else 0}{sl_s}")

    # Individual trade list (sorted by match + game_time)
    print(f"\n{'--- Individual Trades ':{'─'}<60}")
    hdr2 = (f"  {'Match':<30}  {'gt':>5}  {'Event Type':<28}  {'dir':>5}  "
            f"{'fill':>5}  {'lag':>5}  {'edge':>5}  {'15s':>7}  {'30s':>7}  {'60s':>7}  {'120s':>7}  {'SETT':>7}  {'SL':>3}")
    print(hdr2)
    print("  " + "-" * (len(hdr2) - 2))
    for t in sorted(trades, key=lambda x: (x.match_name, x.game_time_sec)):
        name_short = t.match_name.replace("Dota 2: ", "").replace(" Winner", "")[:30]
        sl_flag = " *" if t.stop_loss_exit else "  "
        print(f"  {name_short:<30}  {t.game_time_sec:>5}  {t.event_type:<28}  "
              f"{t.direction:>5}  {t.fill_price:.3f}  {t.lag:.3f}  {t.edge:.3f}  "
              f"{_fmt(t.pnl_15s)}  {_fmt(t.pnl_30s)}  {_fmt(t.pnl_60s)}  {_fmt(t.pnl_120s)}  {_fmt(t.pnl_settle)}  {sl_flag}")

    # CSV export
    if csv_out:
        pnl_cols = [attr for _, attr in _HORIZONS]
        fields = ["match_name", "match_id", "event_type", "direction", "side",
                  "game_time_sec", "entry_ts_ms", "fill_price", "fair_price",
                  "lag", "edge", "spread"] + pnl_cols + ["pnl_settle", "stop_loss_exit"]
        with open(csv_out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for t in sorted(trades, key=lambda x: x.entry_ts_ms):
                w.writerow({field: getattr(t, field) for field in fields})
        print(f"\n  Trades written to {csv_out}")


def print_diagnostics(diagnostics: Counter) -> None:
    print(f"\n{'--- Diagnostics ':{'─'}<60}")
    rejects = {k.removeprefix("reject:"): v for k, v in diagnostics.items() if k.startswith("reject:")}
    accepted= {k.removeprefix("accepted:"): v for k, v in diagnostics.items() if k.startswith("accepted:")}
    seen    = {k.removeprefix("event_seen:"): v for k, v in diagnostics.items() if k.startswith("event_seen:")}

    print(f"  Events detected  : {sum(seen.values())}")
    print(f"  Trades accepted  : {sum(accepted.values())}")

    print("\n  Reject reasons:")
    for reason, cnt in sorted(rejects.items(), key=lambda x: -x[1]):
        print(f"    {reason:<32}  {cnt}")

    print("\n  Events seen vs accepted:")
    for et, cnt in sorted(seen.items(), key=lambda x: -x[1]):
        acc = accepted.get(et, 0)
        print(f"    {et:<32}  seen={cnt}  accepted={acc}  rate={acc/cnt:.0%}")


# ---------------------------------------------------------------------------
# Signal timing analysis
# ---------------------------------------------------------------------------

def print_timing_analysis(trades: list[Trade]) -> None:
    """Per-event-type avg P&L at each fine-grained horizon to find optimal hold time."""
    if not trades:
        return

    by_evt: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        by_evt[t.event_type].append(t)

    # Header: one column per horizon
    horizon_labels = [f"{s}s" for s, _ in _HORIZONS]
    col_w = 8
    print(f"\n{'--- Signal Timing: Avg P&L by Hold Duration ':{'─'}<60}")
    hdr = f"  {'Event Type':<32}" + "".join(f"{lbl:>{col_w}}" for lbl in horizon_labels) + f"  {'N':>4}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    for et in sorted(by_evt):
        ts = by_evt[et]
        row = f"  {et:<32}"
        for _, attr in _HORIZONS:
            vals = [getattr(t, attr) for t in ts if getattr(t, attr) is not None]
            avg  = sum(vals) / len(vals) if vals else None
            row += f"{_fmt(avg):>{col_w}}"
        row += f"  {len(ts):>4}"
        print(row)

    # Also print overall across all event types
    print("  " + "-" * (len(hdr) - 2))
    row = f"  {'ALL':<32}"
    for _, attr in _HORIZONS:
        vals = [getattr(t, attr) for t in trades if getattr(t, attr) is not None]
        avg  = sum(vals) / len(vals) if vals else None
        row += f"{_fmt(avg):>{col_w}}"
    row += f"  {len(trades):>4}"
    print(row)


# ---------------------------------------------------------------------------
# Parameter sweep
# ---------------------------------------------------------------------------

def run_sweep() -> None:
    """Quick sweep over min_lag × min_edge to show sensitivity."""
    print(f"\n{'='*88}")
    print("  PARAMETER SWEEP  (30s / 120s horizons, $5/trade)")
    print(f"{'='*88}")

    print("  Loading data...", end=" ", flush=True)
    _cached = load_data()
    print("done.")

    # Raised comeback caps (from analysis: fill_price_too_high = 32% of skips)
    _RAISED_CAPS = {
        **_EVENT_MAX_FILL,
        "POLL_STOMP_THROW_CONFIRMED":   0.87,
        "POLL_LATE_FIGHT_FLIP":         0.93,
        "POLL_MAJOR_COMEBACK_RECOVERY": 0.87,
        "POLL_COMEBACK_RECOVERY":       0.85,
    }
    _ALL_EVENTS     = set(ACTIVE_EVENTS)   # every defined event type
    _NO_BAD_EVENTS  = _ACTIVE - {"POLL_LEAD_FLIP_WITH_KILLS", "POLL_KILL_BURST_CONFIRMED"}
    # Add comeback events that are currently disabled in .env
    _WITH_COMEBACKS = _ACTIVE | {"POLL_COMEBACK_RECOVERY", "POLL_MAJOR_COMEBACK_RECOVERY"}
    _BEST_EVENTS    = (_WITH_COMEBACKS
                       - {"POLL_LEAD_FLIP_WITH_KILLS", "POLL_KILL_BURST_CONFIRMED"})

    # ── Event / parameter configs ─────────────────────────────────────────────
    ep_configs = [
        # (label, min_lag, min_edge, max_spread, trade_events, fill_caps)
        ("Current config",             0.08, 0.005, 0.12, None,           None),
        ("Lower lag (0.05)",           0.05, 0.005, 0.12, None,           None),
        ("Lower edge (0.003)",         0.08, 0.003, 0.12, None,           None),
        ("Wider spread (0.15)",        0.08, 0.005, 0.15, None,           None),
        ("No LEAD_FLIP/KILL_BURST",    0.08, 0.005, 0.12, _NO_BAD_EVENTS, None),
        ("Raised comeback caps",       0.08, 0.005, 0.12, None,           _RAISED_CAPS),
        ("+COMEBACK_RECOVERY events",  0.08, 0.005, 0.12, _WITH_COMEBACKS,None),
        ("+COMEBACK raised caps",      0.08, 0.005, 0.12, _WITH_COMEBACKS,_RAISED_CAPS),
        ("Best: events+caps+params",   0.05, 0.003, 0.15, _BEST_EVENTS,   _RAISED_CAPS),
    ]

    def _sweep_row(label: str, ts: list[Trade]) -> None:
        p30  = [t.pnl_30s  for t in ts if t.pnl_30s  is not None]
        p120 = [t.pnl_120s for t in ts if t.pnl_120s is not None]
        ps   = [t.pnl_settle for t in ts if t.pnl_settle is not None]
        w30  = sum(1 for v in p30  if v > 0)
        w120 = sum(1 for v in p120 if v > 0)
        ws   = sum(1 for v in ps   if v > 0)
        avg30  = sum(p30) /len(p30)  if p30  else None
        tot30  = sum(p30)            if p30  else None
        avg120 = sum(p120)/len(p120) if p120 else None
        tot120 = sum(p120)           if p120 else None
        avgs   = sum(ps) / len(ps)   if ps   else None
        tots   = sum(ps)             if ps   else None
        wp30   = w30 /len(p30)  if p30  else None
        wp120  = w120/len(p120) if p120 else None
        wps    = ws / len(ps)   if ps   else None
        if wp30 is not None:
            print(f"  {label:<38}  {len(ts):>4}  "
                  f"{_fmt(avg30):>8}  {wp30*100:>4.0f}% | "
                  f"{_fmt(avg120):>8}  {wp120*100:>4.0f}% | "
                  f"{_fmt(avgs):>8}  {wps*100:>4.0f}%  {_fmt(tots):>8}")
        else:
            print(f"  {label:<38}  {len(ts):>4}  {'n/a':>8}  {'n/a':>9}  {'n/a':>6}")

    hdr = (f"  {'Config':<38}  {'N':>4}  "
           f"{'Avg30s':>8}  {'W30%':>5} | {'Avg120s':>8}  {'W120%':>5} | {'AvgSett':>8}  {'WSet%':>5}  {'TotSett':>8}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    for label, ml, me, ms, te, fc in ep_configs:
        ts, _ = run_backtest(
            min_lag=ml, min_edge=me, max_spread=ms, size_usd=PAPER_SIZE_USD,
            trade_events=te, fill_caps=fc, _data=_cached,
        )
        _sweep_row(label, ts)

    # ── Stop-loss sensitivity (current event set, baseline params) ────────────
    print(f"\n{'--- Stop-Loss Sensitivity ':{'─'}<60}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    sl_configs = [
        ("No stop-loss",              None,  None),
        ("SL rel=0.05 (tight)",       0.05,  EXIT_STOP_LOSS_ABS),
        ("SL rel=0.10 (current .env)",0.10,  EXIT_STOP_LOSS_ABS),
        ("SL rel=0.15 (loose)",       0.15,  EXIT_STOP_LOSS_ABS),
        ("SL rel=0.20 (very loose)",  0.20,  EXIT_STOP_LOSS_ABS),
        ("No SL abs floor",           EXIT_STOP_LOSS_REL, None),
    ]
    for label, sl_rel, sl_abs in sl_configs:
        ts, _ = run_backtest(
            min_lag=0.08, min_edge=MIN_EXECUTABLE_EDGE, max_spread=MAX_SPREAD,
            size_usd=PAPER_SIZE_USD, _data=_cached,
            stop_loss_rel=sl_rel, stop_loss_abs=sl_abs,
        )
        _sweep_row(label, ts)

    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay backtest against DreamLeague live data")
    parser.add_argument("--min-lag",        type=float, default=MIN_LAG)
    parser.add_argument("--min-edge",       type=float, default=MIN_EXECUTABLE_EDGE)
    parser.add_argument("--max-spread",     type=float, default=MAX_SPREAD)
    parser.add_argument("--size",           type=float, default=PAPER_SIZE_USD)
    parser.add_argument("--exit",           type=int,   default=30)
    parser.add_argument("--maker-exit",     action="store_true", help="Exit at mid-price (maker) instead of bid (taker)")
    parser.add_argument("--maker-entry",    action="store_true", help="Enter at mid-price (maker) instead of ask (taker)")
    parser.add_argument("--diagnostics",    action="store_true")
    parser.add_argument("--sweep",          action="store_true", help="Run parameter sweep")
    parser.add_argument("--stop-loss-rel",  type=float, default=EXIT_STOP_LOSS_REL,
                        help="Stop-loss: max loss from entry price (default: %(default)s). Set 0 to disable.")
    parser.add_argument("--stop-loss-abs",  type=float, default=EXIT_STOP_LOSS_ABS,
                        help="Stop-loss: absolute floor price (default: %(default)s). Set 0 to disable.")
    parser.add_argument("--max-positions",  type=int,   default=MAX_OPEN_POSITIONS,
                        help="Max simultaneous open positions (default: %(default)s). Set 0 to disable.")
    parser.add_argument("--max-total-usd",  type=float, default=MAX_TOTAL_LIVE_USD,
                        help="Max total USD in open positions (default: %(default)s). Set 0 to disable.")
    parser.add_argument("--csv-out",          type=str,   default=None,
                        help="Write trade-by-trade results to this CSV file.")
    parser.add_argument("--timing",           action="store_true",
                        help="Print signal timing analysis (avg P&L by hold duration)")
    parser.add_argument("--reentry-cooldown", type=int,   default=300,
                        help="Block re-entry in same direction+event within this many seconds (default 300, matches paper_trader).")
    parser.add_argument("--max-game-time",    type=int,   default=0,
                        help="Skip signals with game_time_sec above this (0 = unlimited, e.g. 2400 = 40 min).")
    parser.add_argument("--min-game-time",    type=int,   default=0,
                        help="Skip events before this game time in seconds (default: 0)")
    parser.add_argument("--settle-edge",      type=float, default=None,
                        help="Only compute settlement P&L for trades with edge >= this. Default: all trades.")
    parser.add_argument("--max-exit-stale",   type=int,   default=None,
                        help="Drop horizon PnL when exit book tick is older than this many ms (e.g. 15000). Default: no filter.")
    parser.add_argument("--max-book-age",     type=int,   default=15000,
                        help="Entry book staleness gate in ms (default 15000 = live bot). Raise to recover stale-book rejections.")
    args = parser.parse_args()

    if args.sweep:
        run_sweep()
        sys.exit(0)

    diag = Counter() if args.diagnostics else None
    trades, n_eval = run_backtest(
        min_lag=args.min_lag,
        min_edge=args.min_edge,
        max_spread=args.max_spread,
        size_usd=args.size,
        exit_sec=args.exit,
        exit_at_mid=args.maker_exit,
        entry_at_mid=args.maker_entry,
        diagnostics=diag,
        stop_loss_rel=args.stop_loss_rel or None,
        stop_loss_abs=args.stop_loss_abs or None,
        max_positions=args.max_positions or None,
        max_total_usd=args.max_total_usd or None,
        min_game_time_sec=args.min_game_time,
        max_game_time_sec=args.max_game_time,
        reentry_cooldown_sec=args.reentry_cooldown,
        settle_edge=args.settle_edge,
        max_exit_stale_ms=args.max_exit_stale,
        max_book_age_ms=args.max_book_age,
    )
    print_results(trades, min_lag=args.min_lag, min_edge=args.min_edge,
                  max_spread=args.max_spread, size_usd=args.size, exit_sec=args.exit,
                  n_evaluated=n_eval, csv_out=args.csv_out)
    if diag:
        print_diagnostics(diag)
    if args.timing:
        print_timing_analysis(trades)
