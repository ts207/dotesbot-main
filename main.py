from __future__ import annotations

import asyncio
import traceback
import aiohttp
import time
import os
import json
import fcntl
from datetime import datetime, timezone

from steam_client import fetch_all_live_games
from poly_ws import listen_books, BookStore
from signal_engine import EventSignalEngine, apply_probability_move
from paper_trader import PaperTrader
from storage import SignalLogger, DotaEventLogger, BookEventLogger, PositionLogger, RawSnapshotLogger, LiveAttemptLogger, LatencyLogger, RichContextLogger, SourceDelayLogger, BookRefreshRescueLogger, MatchWinnerSignalLogger, SignalMarkoutLogger, LiveExitLogger, ShadowTradeLogger, BookMoveLogger, ValueAttemptLogger
from value_engine import ValueEngine, ValueSignal, VALUE_ENGINE_ENABLED, ENABLE_VALUE_TRADING
from decisive_swing_engine import DecisiveSwingEngine, DSwingSignal, DSWING_ENABLED
from config import EVENT_DETECTORS_ENABLED
from book_move_detector import BookMoveDetector
from mapping import load_valid_mappings
from event_detector import EventDetector
from live_executor import LiveExecutor, LiveExitExecutor, _to_float
from live_position_store import LivePositionStore, LivePosition
from live_reconciliation import reconcile_live_positions
from live_exit_engine import decide_live_exit
from disk_guard import DiskGuard
from market_scope import is_active_strategy_mapping, is_game3_match_proxy
from mapping_validator import validate_mapping_identity
from hybrid_nowcast import compute_hybrid_nowcast
from realtime_enrichment import maybe_enrich_realtime
from book_refresh import fetch_fresh_book
from event_taxonomy import event_tier, TIER_A_EVENTS, TIER_B_EVENTS
from config import REALTIME_STATS_STALE_SEC
from series_model import compute_bo3_match_p
from team_utils import norm_team

# Current paper runtime has two entry strategies: VALUE and DSWING. Event
# detection remains enabled for diagnostics, but it does not create entries.
ENABLE_EVENT_ENTRY_STRATEGY = False

from config import (
    STEAM_API_KEY, STEAM_POLL_SECONDS, PAPER_EXECUTION_DELAY_MS, LIVE_TRADING,
    ALLOW_CONFIRMATION_ONLY_LIVE_TRADES, MAX_BOOK_AGE_MS, MIN_EXECUTABLE_EDGE,
    MIN_LAG, MAX_SPREAD, MIN_ASK_SIZE_USD, PAPER_SLIPPAGE_CENTS, PAPER_TRADE_SIZE_USD,
    PRICE_LOOKBACK_SEC, REQUIRE_TOP_LIVE_FOR_SIGNALS, DOTA_FAIR_MODEL_PATH,
    MIN_ML_EDGE, ML_STRATEGY_ENABLED,
    ENABLE_MATCH_WINNER_GAME3_PROXY, ENABLE_MATCH_WINNER_RESEARCH,
    ENABLE_REAL_LIVE_TRADING, MAX_TRADE_USD, MAX_STEAM_AGE_MS,
    BOOK_MOVE_GRACE_SEC, BOOK_MOVE_ALPHA_THRESHOLD,
    LIVE_ATTEMPTS_CSV_PATH, PAPER_ATTEMPTS_CSV_PATH,
    PAPER_EXITS_CSV_PATH, PAPER_POSITIONS_PATH,
)
from sync_markets import sync_markets_to_games, load_markets, write_markets
import team_id_cache  # 2026-05-27: backfills empty Steam team_names from team_ids
from discover_markets import main as discover_markets_main
from config import ENABLE_MATCH_WINNER_TRADING
from dota_fair_model.inference import load_bundle
from dota_fair_model.features import build_feature_row

MAPPING_REFRESH_SECONDS = 60
# 2026-06-02 — WS subscription debounce: a token must be absent from the live
# Steam set for this many consecutive mapping-refreshes (×60s) before it's
# dropped from the WS subscription. Stops the 154↔70↔116 flip that forced a
# full WS reconnect every poll and starved the book feed.
WS_SUB_REMOVE_GRACE_POLLS = int(os.getenv("WS_SUB_REMOVE_GRACE_POLLS", "3"))
MARKET_DISCOVER_SECONDS = 600  # re-scrape Polymarket for new G3 markets every 10 min
_LOCK_HANDLE = None


def _acquire_single_instance_lock(path: str = "logs/paper_bot.lock") -> bool:
    """Prevent concurrent bot processes from writing the same runtime logs."""
    global _LOCK_HANDLE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle = open(path, "w", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return False
    handle.write(str(os.getpid()))
    handle.flush()
    _LOCK_HANDLE = handle
    return True


def age_ms(ns: int | None) -> int:
    if not ns:
        return 10 ** 9
    return int((time.time_ns() - ns) / 1_000_000)


def _best_signal_candidate(candidates: list[dict]) -> dict | None:
    """Choose the strongest executable same-poll signal candidate.

    Each candidate is {"signal": dict, "direction": str, "events": list}.
    Prefer executable edge, then expected move. This keeps chaotic same-poll
    updates from entering the first arbitrary direction cluster.
    """
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda c: (
            float(c["signal"].get("executable_edge") or 0.0),
            float(c["signal"].get("expected_move") or 0.0),
        ),
    )


def _book_mid(book: dict | None) -> float | None:
    if not book:
        return None
    bid = book.get("best_bid")
    ask = book.get("best_ask")
    try:
        if bid is not None and ask is not None:
            return (float(bid) + float(ask)) / 2.0
        if ask is not None:
            return float(ask)
        if bid is not None:
            return float(bid)
    except (TypeError, ValueError):
        return None
    return None


def _yes_fair_from_radiant(mapping: dict, game: dict, p_rad: float) -> tuple[float, str] | None:
    side_map = mapping.get("steam_side_mapping")
    if side_map == "normal":
        return p_rad, "radiant"
    if side_map == "reversed":
        return 1.0 - p_rad, "dire"

    yes_team = norm_team(mapping.get("yes_team"))
    radiant_team = norm_team(game.get("radiant_team"))
    dire_team = norm_team(game.get("dire_team"))
    if yes_team and radiant_team and yes_team == radiant_team:
        return p_rad, "radiant"
    if yes_team and dire_team and yes_team == dire_team:
        return 1.0 - p_rad, "dire"
    return None


def _hybrid_context(game: dict) -> dict:
    """Delayed rich context for nowcast adjustments from GetRealtimeStats."""
    ctx: dict = {}
    for key in (
        "aegis_team", "radiant_dead_count", "dire_dead_count",
        "radiant_core_dead_count", "dire_core_dead_count",
        "radiant_max_respawn", "dire_max_respawn", "max_respawn_timer",
        "radiant_level", "dire_level", "realtime_game_time_sec",
        "delayed_game_time_sec", "delayed_field_age_sec",
        "realtime_stats_age_sec",
    ):
        if game.get(key) is not None:
            ctx[key] = game.get(key)
    return ctx


def _hybrid_delay_seconds(game: dict) -> float | None:
    top_gt = game.get("game_time_sec")
    delayed_gt = game.get("realtime_game_time_sec") or game.get("delayed_game_time_sec")
    if top_gt is not None and delayed_gt is not None:
        try:
            return max(0.0, float(top_gt) - float(delayed_gt))
        except (TypeError, ValueError):
            return None
    return game.get("game_time_lag_sec")


def _exit_adverse_position_for_signal(signal: dict, mapping: dict, trader: PaperTrader, book_store: BookStore):
    """Exit an open opposite-side paper position for a valid opposing event."""
    if not signal.get("event_is_primary"):
        return None
    favored_token_id = signal.get("token_id")
    if not favored_token_id:
        return None
    yes_token = mapping.get("yes_token_id")
    no_token = mapping.get("no_token_id")
    if favored_token_id == yes_token:
        opposing_token = no_token
    elif favored_token_id == no_token:
        opposing_token = yes_token
    else:
        return None
    if opposing_token not in trader.positions:
        return None
    return trader.force_exit(opposing_token, book_store, "adverse_event")


async def _log_live_attempt_with_markouts(attempt, book_store: BookStore, live_logger: LiveAttemptLogger):
    """Log submit row immediately, then a final row after 30s with markouts."""
    live_logger.log_attempt(attempt, phase="submit")
    reference = attempt.avg_fill_price or attempt.best_ask or attempt.price_cap
    markouts = {}
    if reference is None:
        await asyncio.sleep(30)
        live_logger.log_attempt(attempt, phase="markout", markouts=markouts)
        return

    async def sample(delay: int) -> float | None:
        await asyncio.sleep(delay)
        mid = _book_mid(book_store.get(attempt.token_id))
        return round(mid - reference, 4) if mid is not None else None

    m3 = await sample(3)
    markouts["markout_3s"] = m3
    m10 = await sample(7)
    markouts["markout_10s"] = m10
    m30 = await sample(20)
    markouts["markout_30s"] = m30
    live_logger.log_attempt(attempt, phase="markout", markouts=markouts)


async def _log_signal_markouts(row: dict, token_id: str, book_store: BookStore, markout_logger: SignalMarkoutLogger):
    """Log post-signal mid-price movement for skipped/filled signal analysis."""
    reference = row.get("reference_price")
    try:
        reference = float(reference) if reference is not None else None
    except (TypeError, ValueError):
        reference = None

    fair = row.get("fair_price") if row.get("fair_price") is not None else row.get("hybrid_fair")
    try:
        fair = float(fair) if fair is not None else None
    except (TypeError, ValueError):
        fair = None

    markouts = {}
    edges = {}

    async def sample(delay: int, label: str):
        await asyncio.sleep(delay)
        mid = _book_mid(book_store.get(token_id))
        markouts[f"markout_{label}"] = round(mid - reference, 4) if mid is not None and reference is not None else None
        edges[f"edge_after_{label}"] = round(fair - mid, 4) if mid is not None and fair is not None else None

    await sample(3, "3s")
    await sample(7, "10s")
    await sample(20, "30s")
    out = dict(row)
    out.update(markouts)
    out.update(edges)
    markout_logger.log_markout(out)


def _normalized_entry_fill(*, filled_usd, filled_shares, avg_fill_price, fallback_price):
    """Return internally consistent (cost, shares, price) for a filled entry."""
    cost = _to_float(filled_usd)
    shares = _to_float(filled_shares)
    price = _to_float(avg_fill_price) or _to_float(fallback_price)

    if cost is not None and cost > 0 and price is not None and price > 0:
        return cost, cost / price, price
    if shares is not None and shares > 0 and price is not None and price > 0:
        return shares * price, shares, price
    if cost is not None and cost > 0 and shares is not None and shares > 0:
        return cost, shares, cost / shares
    return None


# Shared between steam_loop (writer) and proactive_refresh_loop (reader).
# Once a match's game_over flag fires, its tokens are skipped by the periodic
# book refresher so we don't pile up CLOB timeouts on closed markets.
_PERSISTENT_GAME_OVER_MATCH_IDS: set[str] = set()

_VALUE_CONFIRM_STATE: dict[str, dict] = {}


def _value_confirmation_passes(result: ValueSignal) -> tuple[bool, str]:
    """Require persistent VALUE edge before entering real/paper trading."""
    if os.getenv("VALUE_CONFIRM_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        return True, "disabled"

    min_edge = float(os.getenv("VALUE_CONFIRM_MIN_EDGE", "0.12"))
    max_age_sec = float(os.getenv("VALUE_CONFIRM_MAX_AGE_SEC", "90"))
    max_ask_worsen = float(os.getenv("VALUE_CONFIRM_MAX_ASK_WORSEN", "0.02"))
    key = f"{result.match_id}|{result.token_id}|{result.side}"
    now_ns = int(result.received_at_ns or time.time_ns())

    prior = _VALUE_CONFIRM_STATE.get(key)
    if result.edge < min_edge:
        _VALUE_CONFIRM_STATE.pop(key, None)
        return False, f"value_confirm_edge_too_low:edge={result.edge:.4f}_min={min_edge:.4f}"

    if not prior:
        _VALUE_CONFIRM_STATE[key] = {
            "received_at_ns": now_ns,
            "ask": result.ask,
            "edge": result.edge,
            "signal_id": result.signal_id,
        }
        return False, "value_confirm_armed"

    age_sec = max(0.0, (now_ns - int(prior["received_at_ns"])) / 1e9)
    ask_worsen = float(result.ask) - float(prior["ask"])
    if age_sec > max_age_sec:
        _VALUE_CONFIRM_STATE[key] = {
            "received_at_ns": now_ns,
            "ask": result.ask,
            "edge": result.edge,
            "signal_id": result.signal_id,
        }
        return False, f"value_confirm_expired:age={age_sec:.1f}_max={max_age_sec:.1f}"
    if ask_worsen > max_ask_worsen:
        _VALUE_CONFIRM_STATE[key] = {
            "received_at_ns": now_ns,
            "ask": result.ask,
            "edge": result.edge,
            "signal_id": result.signal_id,
        }
        return False, f"value_confirm_ask_worsened:delta={ask_worsen:.4f}_max={max_ask_worsen:.4f}"

    _VALUE_CONFIRM_STATE.pop(key, None)
    return True, f"value_confirmed:age={age_sec:.1f}_ask_delta={ask_worsen:.4f}"


def _load_game_over_match_ids_from_csv(path: str = "logs/raw_snapshots.csv") -> set[str]:
    """Pre-populate the game-over set from prior-session snapshots so a fresh
    restart doesn't re-poll books for matches that already ended.
    """
    import csv as _csv
    finished: set[str] = set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                go = str(row.get("game_over", "")).strip().lower()
                if go in {"true", "1", "yes"}:
                    mid = str(row.get("match_id") or "").strip()
                    if mid:
                        finished.add(mid)
    except OSError:
        pass
    return finished


async def _handle_manual_order(
    order: dict,
    trader: PaperTrader,
    book_store: BookStore,
    live_executor: LiveExecutor | None,
    position_logger: PositionLogger | None = None,
) -> dict:
    """Dispatch a dashboard-queued manual order through the bot's books.

    Paper mode: uses PaperTrader.enter / force_exit just like the event engine.
    Live mode: routes through LiveCLOBClient (buy_fak_market / sell_gtc_limit).
    """
    from config import ENABLE_REAL_LIVE_TRADING
    action = order.get("action")
    token_id = str(order.get("token_id") or "")
    match_id = str(order.get("match_id") or "")
    if not token_id:
        return {"status": "error", "error": "missing token_id"}

    # Resolve mapping (needed for tick_size, neg_risk, market_name)
    from mapping import load_valid_mappings
    valid, _ = load_valid_mappings()
    mapping = next(
        (m for m in valid if m.get("yes_token_id") == token_id or m.get("no_token_id") == token_id),
        None,
    )
    if mapping is None:
        return {"status": "error", "error": f"no mapping for token {token_id[:12]}..."}
    side = "YES" if mapping.get("yes_token_id") == token_id else "NO"
    opposing = mapping.get("no_token_id") if side == "YES" else mapping.get("yes_token_id")

    if action == "buy":
        size_usd = float(order.get("size_usd") or 0)
        if size_usd <= 0:
            return {"status": "error", "error": "size_usd must be > 0"}
        signal = {
            "size_usd": size_usd,
            "event_type": "MANUAL",
            "manual": True,
            "price_cap": float(order.get("price_cap") or 0) or None,
        }
        pos, reason = trader.enter(
            signal=signal,
            token_id=token_id,
            side=side,
            book_store=book_store,
            match_id=match_id or str(mapping.get("dota_match_id") or ""),
            market_name=mapping.get("name"),
            opposing_token_id=opposing or "",
        )
        if pos is None:
            return {"status": "rejected", "reason": reason}
        # Log the entry to paper_trades.csv so dashboard sees it
        if position_logger is not None:
            try:
                position_logger.log_entry(pos)
            except Exception as _le:
                print(f"manual buy log_entry failed (non-fatal): {_le}")
        print(f"MANUAL ENTER {pos.match_id} {pos.side} price={pos.entry_price:.4f} cost=${pos.cost_usd:.2f}")
        return {
            "status": "filled" if not ENABLE_REAL_LIVE_TRADING else "submitted",
            "mode": "paper" if not ENABLE_REAL_LIVE_TRADING else "live",
            "entry_price": pos.entry_price,
            "shares": pos.shares,
            "match_id": pos.match_id,
        }

    if action == "exit":
        closed = trader.force_exit(token_id, book_store, reason="manual_exit")
        if closed is None:
            return {"status": "error", "error": "no open position for that token"}
        return {
            "status": "exited",
            "mode": "paper" if not ENABLE_REAL_LIVE_TRADING else "live",
            "exit_price": closed.exit_price,
            "pnl_usd": closed.pnl_usd,
            "match_id": closed.match_id,
        }

    return {"status": "error", "error": f"unknown action: {action}"}


async def steam_loop(
    book_store: BookStore,
    trader: PaperTrader,
    signal_logger: SignalLogger,
    event_detector: EventDetector,
    signal_engine: EventSignalEngine,
    event_logger: DotaEventLogger,
    position_logger: PositionLogger,
    snapshot_logger: RawSnapshotLogger,
    latency_logger: LatencyLogger,
    live_executor: LiveExecutor | None,
    live_logger: LiveAttemptLogger | None,
    rich_context_logger: RichContextLogger,
    source_delay_logger: SourceDelayLogger,
    rescue_logger: BookRefreshRescueLogger,
    match_winner_logger: MatchWinnerSignalLogger,
    signal_markout_logger: SignalMarkoutLogger,
    mappings: list[dict],
    asset_ids: list[str],
    model_bundle: Any | None = None,
    http_session: aiohttp.ClientSession | None = None,
    live_position_store: LivePositionStore | None = None,
    live_exit_executor: LiveExitExecutor | None = None,
    live_exit_logger: LiveExitLogger | None = None,
    check_live_exits_fn: Any | None = None,
    shadow_logger: ShadowTradeLogger | None = None,
    last_steam_games: dict | None = None,
    pending_book_moves: list[dict] | None = None,
    value_engine: ValueEngine | None = None,
    value_logger: ValueAttemptLogger | None = None,
    dswing_engine: DecisiveSwingEngine | None = None,
    dswing_logger: DSwingAttemptLogger | None = None,
):
    if not STEAM_API_KEY or STEAM_API_KEY == "replace_me":
        print("Missing STEAM_API_KEY. Copy .env.example to .env and fill it in.")
        return

    last_mapping_refresh = 0.0
    _sub_absent_polls: dict[str, int] = {}  # token -> consecutive refreshes absent from live set
    # Do not block the first Steam poll on a full Polymarket discovery scrape.
    # The supervised binder handles startup binding; this in-loop discovery is a
    # periodic refresh and can wait for the normal interval.
    last_market_discover = time.monotonic()
    max_game_times: dict[str, int] = {}  # match_id -> max game_time_sec seen

    # Load team win stats for ML features
    team_stats = {}
    stats_path = "dota_fair_model/models/team_stats.json"
    if os.path.exists(stats_path):
        try:
            with open(stats_path, "r") as f:
                team_stats = json.load(f)
            print(f"Loaded {len(team_stats)} team win ratios from {stats_path}")
        except Exception as e:
            print(f"Failed to load team stats: {e}")

    async with aiohttp.ClientSession() as session:
        _iter_debug = 0
        _last_iter_print = 0.0
        while True:
            try:
                import time as _time
                now = _time.monotonic()
                _iter_debug += 1
                # Print first iter, then every 30s of wall time (regardless of iter count)
                if _iter_debug == 1 or (now - _last_iter_print) >= 30.0:
                    print(f"DEBUG steam_loop iter={_iter_debug} t={now:.1f}")
                    _last_iter_print = now

                # Drain manual orders from dashboard (one per tick)
                try:
                    from manual_orders import drain as _drain_manual, mark_processed as _mark_manual
                    for _mo in _drain_manual():
                        try:
                            _res = await _handle_manual_order(
                                _mo, trader, book_store, live_executor,
                                position_logger=position_logger,
                            )
                        except Exception as _mo_exc:
                            _res = {"status": "error", "error": str(_mo_exc)}
                        _mark_manual(_mo["id"], _res)
                        print(f"[manual_order] {_mo.get('action')} id={_mo['id'][:8]} -> {_res.get('status')}")
                except Exception as _mo_loop_exc:
                    print(f"manual-order drain error (non-fatal): {_mo_loop_exc}")
                if now - last_market_discover >= MARKET_DISCOVER_SECONDS:
                    last_market_discover = now
                    try:
                        await discover_markets_main(auto_write=True)
                    except Exception as _disc_exc:
                        print(f"Market discovery error (non-fatal): {_disc_exc}")

                if now - last_mapping_refresh >= MAPPING_REFRESH_SECONDS:
                    last_mapping_refresh = now
                    games_for_sync = await fetch_all_live_games(session, include_league=True)
                    # Learn team_id → team_name from games that have BOTH, then
                    # backfill names on games that have team_id but no team_name.
                    team_id_cache.observe_many(games_for_sync)
                    for _g in games_for_sync: team_id_cache.backfill_team_names(_g)
                    mdata = load_markets()
                    raw_markets = mdata.setdefault("markets", [])
                    updates = sync_markets_to_games(raw_markets, games_for_sync)
                    if updates:
                        write_markets(mdata)
                        for u in updates:
                            print(
                                f"AUTO-MAPPED {u['market_name']} → {u['dota_match_id']} "
                                f"({u['radiant_team']} vs {u['dire_team']})"
                            )
                    fresh_mappings, _ = load_valid_mappings()
                    fresh_mappings = [
                        m for m in fresh_mappings
                        if is_active_strategy_mapping(
                            m,
                            enable_match_winner_game3_proxy=ENABLE_MATCH_WINNER_GAME3_PROXY,
                            enable_match_winner_research=ENABLE_MATCH_WINNER_RESEARCH,
                            enable_match_winner_trading=ENABLE_MATCH_WINNER_TRADING,
                        )
                    ]
                    # 2026-05-27: also drop mappings whose dota_match_id is known
                    # to be game_over (yesterday's settled matches). Polymarket
                    # sends zero updates for these, which would otherwise keep the
                    # WS churning in a heartbeat-timeout / reconnect loop.
                    _before = len(fresh_mappings)
                    fresh_mappings = [
                        m for m in fresh_mappings
                        if str(m.get("dota_match_id") or "") not in _PERSISTENT_GAME_OVER_MATCH_IDS
                    ]
                    _dropped = _before - len(fresh_mappings)
                    if _dropped:
                        print(f"WS_SUB_FILTER dropped {_dropped} mappings for game_over matches")
                    # 2026-05-29: filter to currently-live Steam matches.
                    # markets.yaml accumulates settled markets that get scanned
                    # forever as `incomplete_book` and bloat the WS subscription
                    # (observed: 710 subs, 3.7% actually trading; 99.89% of arb
                    # scans wasted). Defensive: if Steam returned nothing this
                    # tick (API hiccup), keep the previous live set.
                    # 2026-05-30: also guard against PARTIAL Steam responses.
                    # GetLiveLeagueGames can return a small subset under load.
                    # If the new live set would shrink the WS sub by more than
                    # 50% (and current sub had > 5 tokens), treat as a hiccup
                    # and keep the previous set. Game-over pruning still works
                    # via _PERSISTENT_GAME_OVER_MATCH_IDS (handled above).
                    _live_match_ids = {str(g.get("match_id") or "") for g in games_for_sync if g.get("match_id")}
                    # 2026-06-02 — WS subscription STABILITY fix. The old logic
                    # expanded the sub to ALL mappings whenever Steam momentarily
                    # returned no live matches, then re-narrowed next poll. That
                    # 154↔70↔116 flip forced a full WS teardown+reconnect every
                    # ~60s (and the handshake often timed out), so the connection
                    # never held long enough to stream book updates → books went
                    # stale → ~0 trades. New behaviour: ADD live tokens promptly;
                    # only DROP a token after it's been absent from the live set
                    # for WS_SUB_REMOVE_GRACE_POLLS consecutive refreshes; an empty
                    # Steam response (hiccup) neither adds nor penalizes anything.
                    _valid_tokens = {tid for m in fresh_mappings
                                     for tid in (m["yes_token_id"], m["no_token_id"])}
                    _prev_assets = set(asset_ids)
                    if _live_match_ids:
                        _candidate_assets = {tid for m in fresh_mappings
                                             if str(m.get("dota_match_id") or "") in _live_match_ids
                                             for tid in (m["yes_token_id"], m["no_token_id"])}
                        # Partial-response guard: GetLiveLeagueGames can return a
                        # small subset under load. If the live set covers <50% of
                        # the current (valid) sub, treat this poll as a suspect
                        # partial — still ADD genuinely-new live tokens, but do NOT
                        # accrue absences (no drops) so a sustained partial can't
                        # drain live subscriptions.
                        _prev_valid = _prev_assets & _valid_tokens
                        _suspect_partial = (len(_prev_valid) > 5 and
                                            len(_candidate_assets & _prev_valid) < len(_prev_valid) * 0.5)
                        for tid in _prev_assets:
                            if tid not in _valid_tokens:
                                _sub_absent_polls[tid] = WS_SUB_REMOVE_GRACE_POLLS  # mapping gone → drop
                            elif tid in _candidate_assets:
                                _sub_absent_polls.pop(tid, None)                    # live → reset
                            elif not _suspect_partial:
                                _sub_absent_polls[tid] = _sub_absent_polls.get(tid, 0) + 1
                    else:
                        # Steam hiccup: no live info. Don't penalize live tokens;
                        # still drop tokens whose mapping no longer exists.
                        _candidate_assets = set()
                        for tid in _prev_assets:
                            if tid not in _valid_tokens:
                                _sub_absent_polls[tid] = WS_SUB_REMOVE_GRACE_POLLS

                    new_live_assets = {tid for tid in _prev_assets
                                       if _sub_absent_polls.get(tid, 0) < WS_SUB_REMOVE_GRACE_POLLS}
                    new_live_assets |= _candidate_assets
                    for tid in list(_sub_absent_polls):           # forget tokens no longer subscribed
                        if tid not in new_live_assets:
                            _sub_absent_polls.pop(tid, None)
                    live_mappings = [m for m in fresh_mappings
                                     if m["yes_token_id"] in new_live_assets
                                     or m["no_token_id"] in new_live_assets]

                    new_full_pairs = {(m["yes_token_id"], m["no_token_id"]) for m in fresh_mappings}
                    old_full_pairs = {(m["yes_token_id"], m["no_token_id"]) for m in mappings}
                    current_assets = set(asset_ids)

                    if new_full_pairs != old_full_pairs:
                        mappings.clear()
                        mappings.extend(fresh_mappings)
                        added = new_full_pairs - old_full_pairs
                        removed = old_full_pairs - new_full_pairs
                        if added:
                            print(f"Mappings added: {len(added)} market(s). Restart not required.")
                        if removed:
                            print(f"Mappings removed: {len(removed)} market(s).")

                    if new_live_assets != current_assets:
                        asset_ids.clear()
                        asset_ids.extend(new_live_assets)
                        print(f"WS_SUB live-only: {len(live_mappings)} live market(s); "
                              f"{len(fresh_mappings) - len(live_mappings)} stale skipped")

                if _iter_debug == 1 or (now - _last_iter_print) < 0.1: print(f"DEBUG iter={_iter_debug} pre-fetch_all")
                games = await fetch_all_live_games(session, include_league=True)
                # Backfill empty team names from team_id cache (Valve API regression workaround)
                team_id_cache.observe_many(games)
                for _g in games: team_id_cache.backfill_team_names(_g)
                if _iter_debug == 1 or (now - _last_iter_print) < 0.1:
                    print(f"DEBUG iter={_iter_debug} post-fetch_all games={len(games)} "
                          f"team_id_cache={team_id_cache.cache_size()}")

                # Filter to only 'tracked' games: those that are already mapped
                # OR are candidates for mapping (matching teams in our list).
                tracked_match_ids = {str(m["dota_match_id"]) for m in mappings if m.get("dota_match_id")}
                # Also include games that sync_markets might want to see
                mdata_all = load_markets()
                all_raw_markets = mdata_all.get("markets", [])
                
                def is_relevant(g):
                    mid = str(g.get("match_id") or "")
                    if mid in tracked_match_ids:
                        return True
                    # Original fuzzy match for discovering unmapped markets
                    from sync_markets import match_direction
                    return any(match_direction(m, g) for m in all_raw_markets)

                relevant_games = [g for g in games if is_relevant(g)]
                if _iter_debug == 1 or (now - _last_iter_print) < 0.1:
                    print(f"DEBUG iter={_iter_debug} relevant_games={len(relevant_games)} tracked={len(tracked_match_ids)}")

                # Per-tick game_over set drives exit checks below. We also mirror
                # it into a persistent set on steam_loop's nonlocal frame
                # (_persistent_game_over_match_ids) so proactive_refresh_loop
                # stops polling books for tokens of finished matches.
                game_over_match_ids: set[str] = set()
                current_game_times: dict[str, int | None] = {}

                # 1. First pass: log and process only relevant games
                active_games = []
                for game in relevant_games:
                    match_id = str(game.get("match_id") or "")
                    game_time = game.get("game_time_sec")
                    data_source = game.get("data_source")

                    snapshot_logger.log_game(game)

                    # Attach LiveLeague context as metadata (non-blocking,
                    # never changes expected_move/edge/sizing)
                    # GetRealtimeStats: delayed rich context (heroes, net worth, deaths,
                    # aegis). Must not overwrite fast GetTopLiveGame fields.
                    await maybe_enrich_realtime(game, session)

                    # Annotate realtime freshness status for downstream use
                    rt_age = game.get("realtime_stats_age_sec")
                    game["realtime_context_status"] = (
                        "fresh" if rt_age is not None and rt_age < REALTIME_STATS_STALE_SEC else "stale"
                    )
                    game["game_time_lag_sec"] = _hybrid_delay_seconds(game)

                    # Inject team win ratios from historical stats
                    r_id = str(game.get("radiant_team_id") or "")
                    d_id = str(game.get("dire_team_id") or "")
                    game["radiant_team_win_ratio"] = team_stats.get(r_id, 0.5)
                    game["dire_team_win_ratio"] = team_stats.get(d_id, 0.5)

                    # Log the final enriched rich context for this match
                    rich_context_logger.log_rich_context(game)

                    # Validate mapping identity
                    for mapping in mappings:
                        if str(mapping.get("dota_match_id") or "") in {match_id, str(game.get("lobby_id") or "")}:
                            mapping_result = validate_mapping_identity(mapping, game)
                            game["mapping_confidence"] = mapping_result.mapping_confidence
                            game["mapping_errors"] = ";".join(mapping_result.mapping_errors)
                            game["team_id_match"] = mapping_result.team_id_match
                            game["market_game_number_match"] = mapping_result.market_game_number_match
                            game["duplicate_match_id_error"] = mapping_result.duplicate_match_id_error
                            if mapping_result.mapping_errors:
                                print(f"MAPPING MISMATCH {match_id}: {game['mapping_errors']}")

                    # Log RealtimeStats delay for analysis
                    rt_delayed_gt = game.get("delayed_game_time_sec")
                    top_gt = game.get("game_time_sec")
                    if rt_delayed_gt is not None:
                        source_delay_logger.log_source_delay({
                            "match_id": match_id,
                            "lobby_id": game.get("lobby_id"),
                            "league_id": game.get("league_id"),
                            "realtime_game_time_sec": rt_delayed_gt,
                            "toplive_game_time_sec": top_gt,
                            "game_time_lag_sec": game.get("game_time_lag_sec"),
                            "realtime_stats_age_sec": rt_age,
                            "realtime_context_status": game.get("realtime_context_status"),
                        })

                    # Guard: Ignore non-TopLive sources for event detection if required
                    if REQUIRE_TOP_LIVE_FOR_SIGNALS and data_source != "top_live":
                        continue

                    # Guard: Ignore backward-moving game time (stale/out-of-order snapshots)
                    if game_time is not None:
                        prev_max = max_game_times.get(match_id, -1)
                        if game_time < prev_max:
                            continue
                        max_game_times[match_id] = game_time

                    current_game_times[match_id] = game_time
                    if game.get("game_over"):
                        game_over_match_ids.add(match_id)
                        _PERSISTENT_GAME_OVER_MATCH_IDS.add(match_id)
                    else:
                        active_games.append(game)

                # Update shared steam snapshot dict for book_move_detector cross-reference
                if last_steam_games is not None:
                    last_steam_games.clear()
                    for _g in active_games:
                        _mid = str(_g.get("match_id") or "")
                        if _mid:
                            last_steam_games[_mid] = _g

                # Check exits before processing new signals.
                # For underdog reversal positions, also exit if leader grabs Aegis.
                _aegis_adverse: set[str] = set()
                for _tok, _pos in trader.positions.items():
                    if not _pos.is_underdog_reversal:
                        continue
                    _g = last_steam_games.get(_pos.match_id) if last_steam_games else None
                    if not _g:
                        continue
                    _derived = _g.get("realtime_derived_events") or []
                    try:
                        _rlead = int(_g.get("radiant_lead") or 0)
                    except (TypeError, ValueError):
                        _rlead = 0
                    if _rlead >= 2000 and "AEGIS_HELD_BY_RADIANT" in _derived:
                        _aegis_adverse.add(_tok)
                    elif _rlead <= -2000 and "AEGIS_HELD_BY_DIRE" in _derived:
                        _aegis_adverse.add(_tok)

                closed = trader.check_exits(
                    book_store,
                    game_over_match_ids,
                    current_game_times,
                    adverse_token_ids=_aegis_adverse,
                )
                if check_live_exits_fn:
                    asyncio.create_task(check_live_exits_fn(game_over_match_ids=game_over_match_ids))

                # Process pending alpha candidates
                now_time = time.time()
                still_pending = []
                if pending_book_moves is not None:
                    for pending in pending_book_moves:
                        if now_time - pending["queued_at"] > BOOK_MOVE_GRACE_SEC:
                            continue # Expired
                        
                        p_match_id = pending["match_id"]
                        game = last_steam_games.get(p_match_id) if last_steam_games is not None else None
                        if not game:
                            still_pending.append(pending)
                            continue
                            
                        # Re-evaluate steam corroboration with new snapshot
                        radiant_lead = game.get("radiant_lead")
                        if radiant_lead is None:
                            still_pending.append(pending)
                            continue
                            
                        m = pending["mapping"]
                        yes_team = (m.get("yes_team") or "").strip().lower()
                        steam_radiant = (m.get("steam_radiant_team") or game.get("radiant_team") or "").strip().lower()
                        yes_is_radiant = bool(yes_team and steam_radiant and yes_team == steam_radiant)
                        
                        steam_yes_direction = "up" if (yes_is_radiant and radiant_lead > 0) or \
                                                      (not yes_is_radiant and radiant_lead < 0) else "down"
                        sig = pending["sig"]
                        exec_tok_side = pending["exec_tok_side"]
                        expected_direction = "up" if exec_tok_side == "YES" else "down"
                        
                        if steam_yes_direction == expected_direction:
                            print(f"LATE CORROBORATION: {m.get('name')} {exec_tok_side} after {now_time - pending['queued_at']:.2f}s")
                            sig["steam_corroborated"] = True
                            sig["trade_skip_reason"] = None
                            
                            # Dedup
                            exec_tok_id = pending["exec_tok_id"]
                            # _book_move_last_exec is defined in main(), so this is a closure scope access in _on_book_update.
                            # But wait, steam_loop is separate from main(). I need to handle this.
                            # For now, I'll pass a dummy _book_move_last_exec or skip dedup here since it's already deduped before queueing.
                            
                            # Fair price and signal
                            exec_ask = pending["exec_ask"]
                            exec_bid = pending["exec_bid"]
                            exec_spread = pending["exec_spread"]
                            exec_book = pending["exec_book"]
                            exec_opp_tok = pending["exec_opp_tok"]
                            
                            _exec_mid = (float(exec_ask) + float(exec_bid)) / 2 if exec_bid is not None else float(exec_ask)

                            # Scale magnitude for MATCH_WINNER (Series) markets
                            expected_move = abs(sig["magnitude"])
                            if m.get("market_type") == "MATCH_WINNER":
                                try:
                                    p_next = float(m.get("p_next_yes") or 0.5)
                                    p_next = max(0.01, min(0.99, p_next))
                                    score_yes = int(m.get("series_score_yes") or 0)
                                    score_no = int(m.get("series_score_no") or 0)
                                    gnum = int(m.get("current_game_number") or m.get("game_number") or 1)
                                    if gnum == 1:
                                        sensitivity = 2 * p_next * (1 - p_next)
                                    elif gnum == 2:
                                        sensitivity = (1 - p_next) if score_yes >= score_no else p_next
                                    else:
                                        sensitivity = 1.0
                                except (TypeError, ValueError):
                                    sensitivity = 0.5
                                expected_move *= sensitivity
                            
                            _bm_fair = min(_exec_mid + expected_move, 0.95)
                            trade_signal = {
                                "event_type": "BOOK_MOVE_ALPHA",
                                "event_schema_version": "cadence_v1",
                                "source_cadence_quality": "delayed_corroboration",
                                "token_id": exec_tok_id,
                                "fair_price": _bm_fair,
                                "executable_edge": _bm_fair - float(exec_ask),
                                "ask": float(exec_ask),
                                "bid": float(exec_bid) if exec_bid is not None else None,
                                "spread": exec_spread,
                                "ask_size": exec_book.get("ask_size"),
                                "expected_move": abs(sig["magnitude"]),
                                "lag": abs(sig["magnitude"]),
                                "target_size_usd": MAX_TRADE_USD,
                                "magnitude": sig["magnitude"],
                                "direction": sig["direction"],
                                "book_age_ms": sig.get("book_age_ms"),
                                "max_fill_price": 0.94,
                            }
                            
                            pos, reason = trader.enter(
                                signal=trade_signal,
                                token_id=exec_tok_id,
                                side=exec_tok_side,
                                book_store=book_store,
                                match_id=p_match_id,
                                market_name=m.get("name"),
                                opposing_token_id=exec_opp_tok,
                            )
                            if pos:
                                sig["traded"] = True
                                position_logger.log_entry(pos)
                                print(
                                    f"BOOK_MOVE_ALPHA ENTER {m.get('name','?')[:30]} {exec_tok_side} "
                                    f"price={pos.entry_price:.4f} cost=${pos.cost_usd:.2f}"
                                )
                        else:
                            still_pending.append(pending)
                            
                    if pending_book_moves is not None:
                        pending_book_moves[:] = still_pending
                
                for cp in closed:
                    position_logger.log_exit(cp)
                    print(
                        f"EXIT [{cp.exit_reason}] {cp.market_name} {cp.side} "
                        f"entry={cp.entry_price:.4f} exit={cp.exit_price:.4f} "
                        f"pnl=${cp.pnl_usd:+.2f} hold={cp.hold_sec:.0f}s"
                    )

                # 2026-05-30: Stop running detectors on unmapped tournament
                # matches. Previously this fired ~20% of the funnel as
                # no_mapping_for_match skip rows that we can't act on. Auto-
                # mapper still observes these games via the steam_loop poll
                # and binds them when Polymarket lists a market.

                for game in active_games:
                    for mapping in mappings:
                        if str(mapping["dota_match_id"]) not in {
                            game.get("match_id"), game.get("lobby_id")
                        }:
                            continue

                        yes_book = book_store.get(mapping["yes_token_id"])
                        no_book = book_store.get(mapping["no_token_id"])

                        # Record prices into rolling history (drives lag calculation)
                        game_time = game.get("game_time_sec")
                        for tok, book in [
                            (mapping["yes_token_id"], yes_book),
                            (mapping["no_token_id"], no_book),
                        ]:
                            if book and book.get("best_bid") is not None and book.get("best_ask") is not None:
                                book_mid = (book["best_bid"] + book["best_ask"]) / 2
                                signal_engine.record_price(tok, book_mid, game_time)

                        # --- Value Engine ---
                        if value_engine is not None and value_logger is not None:
                            # tokens already held for THIS market — enables the opposite-side hedge gate
                            _mkt_toks = {str(mapping.get("yes_token_id")), str(mapping.get("no_token_id"))}
                            _entered_toks = ({
                                str(p.token_id) for p in live_position_store.positions.values()
                                if p.state in {"OPEN", "EXITING", "PENDING_ENTRY", "PENDING_EXIT_GTC"}
                                and str(p.token_id) in _mkt_toks
                            } if live_position_store else set())
                            value_results = value_engine.evaluate(game, mapping, book_store, entered_tokens=_entered_toks)
                            for result in value_results:
                                if not isinstance(result, ValueSignal):
                                    value_logger.log_reject(result)
                                    continue
                                value_logger.log_signal(result)
                                if not ENABLE_VALUE_TRADING:
                                    continue
                                opposing_tok = mapping["no_token_id"] if result.token_id == mapping["yes_token_id"] else mapping["yes_token_id"]
                                # Skip only if we already hold THIS side (no double-buy). The
                                # OPPOSITE side is ALLOWED (offset/hedge in a swingy match); the
                                # engine already applied the looser hedge gate (fair>0.5, edge>=0.04) to it.
                                if str(result.token_id) in _entered_toks:
                                    continue
                                confirmed, confirm_reason = _value_confirmation_passes(result)
                                if not confirmed:
                                    print(
                                        f"VALUE LIVE WAIT {mapping.get('name')} {result.side} "
                                        f"reason={confirm_reason} edge={result.edge:.4f} ask={result.ask:.4f}"
                                    )
                                    continue

                                # Guarded executor path. In paper mode
                                # try_buy_value returns a paper_simulated fill
                                # and logs to paper_attempts.csv; with real-live
                                # enabled it routes through the CLOB client.
                                if live_executor is not None and live_position_store is not None:
                                    v_attempt = await live_executor.try_buy_value(
                                        signal=result, mapping=mapping, game=game, book_store=book_store)
                                    if live_logger is not None:
                                        live_logger.log_attempt(v_attempt, phase="entry")
                                    # Record delayed/live FAKs as pending only. Promote
                                    # to OPEN after a terminal fill response so shares,
                                    # price, and cost stay internally consistent.
                                    _landed = (v_attempt.filled_size_usd > 0
                                               or v_attempt.order_status in ("delayed", "live", "matched", "filled"))
                                    if _landed:
                                        entry_px = v_attempt.avg_fill_price or v_attempt.price_cap or result.ask
                                        fill = _normalized_entry_fill(
                                            filled_usd=v_attempt.filled_size_usd,
                                            filled_shares=None,
                                            avg_fill_price=v_attempt.avg_fill_price,
                                            fallback_price=entry_px,
                                        )
                                        is_filled = fill is not None and v_attempt.filled_size_usd > 0
                                        if fill:
                                            cost, v_shares, entry_px = fill
                                        else:
                                            cost = v_attempt.submitted_size_usd or 0.0
                                            v_shares = 0.0
                                        v_pos = LivePosition(
                                            position_id=f"{v_attempt.match_id}:{v_attempt.token_id}:{v_attempt.created_at_ns}",
                                            state="OPEN" if is_filled else "PENDING_ENTRY",
                                            token_id=v_attempt.token_id,
                                            opposing_token_id=opposing_tok or "",
                                            match_id=v_attempt.match_id,
                                            market_name=mapping.get("name"),
                                            side=result.side,
                                            entry_price=entry_px,
                                            shares=v_shares,
                                            cost_usd=cost,
                                            entry_time_ns=v_attempt.created_at_ns,
                                            entry_game_time_sec=result.game_time_sec,
                                            event_type="VALUE",
                                            expected_move=0.0,
                                            fair_price=result.fair_price,
                                            trader_kind="value",
                                            exit_horizon_sec=None,
                                            signal_id=result.signal_id,
                                            backed_direction=result.direction,
                                            pending_entry_order_id=v_attempt.order_id if not is_filled else None,
                                        )
                                        live_position_store.add(v_pos)
                                        print(f"VALUE LIVE ENTER {mapping.get('name')} {result.side} entry≈{entry_px:.4f} edge={result.edge:.4f} status={v_attempt.order_status}")
                                    else:
                                        print(f"VALUE LIVE REJECT {mapping.get('name')} {result.side} reason={v_attempt.reason_if_rejected}")
                                else:
                                    # Paper path (PaperTrader simulated fills).
                                    pos, reason = trader.enter(
                                        signal=result.to_signal_dict(),
                                        token_id=result.token_id,
                                        side=result.side,
                                        book_store=book_store,
                                        match_id=str(game.get("match_id") or ""),
                                        market_name=mapping.get("name"),
                                        opposing_token_id=opposing_tok,
                                    )
                                    if pos:
                                        position_logger.log_entry(pos)
                                        print(f"VALUE ENTER {mapping.get('name')} {result.side} price={pos.entry_price:.4f} edge={result.edge:.4f}")

                        # --- Decisive-Swing ML sniper ---
                        if dswing_engine is not None:
                            for ds_res in dswing_engine.evaluate(game, mapping, book_store):
                                if not isinstance(ds_res, DSwingSignal):
                                    if dswing_logger is not None:
                                        dswing_logger.log_reject(ds_res, mapping=mapping)
                                    continue
                                if dswing_logger is not None:
                                    dswing_logger.log_signal(ds_res, mapping=mapping)
                                if not DSWING_ENABLED:
                                    continue
                                ds_opp = mapping["no_token_id"] if ds_res.token_id == mapping["yes_token_id"] else mapping["yes_token_id"]
                                if live_position_store:
                                    _act = {p.token_id for p in live_position_store.positions.values()
                                            if p.state in {"OPEN", "EXITING", "PENDING_ENTRY", "PENDING_EXIT_GTC"}}
                                    if str(ds_res.token_id) in _act or (ds_opp and str(ds_opp) in _act):
                                        continue
                                if live_executor is not None and live_position_store is not None:
                                    a = await live_executor.try_buy_value(signal=ds_res, mapping=mapping, game=game, book_store=book_store)
                                    if live_logger is not None:
                                        live_logger.log_attempt(a, phase="dswing_entry")
                                    if a.filled_size_usd > 0 or a.order_status in ("delayed", "live", "matched", "filled"):
                                        epx = a.avg_fill_price or a.price_cap or ds_res.ask
                                        cost = a.filled_size_usd or a.submitted_size_usd or 0.0
                                        live_position_store.add(LivePosition(
                                            position_id=f"{a.match_id}:{a.token_id}:{a.created_at_ns}",
                                            state="OPEN", token_id=a.token_id, opposing_token_id=ds_opp or "",
                                            match_id=a.match_id, market_name=mapping.get("name"), side=ds_res.side,
                                            entry_price=epx, shares=(cost / epx if epx else 0.0), cost_usd=cost,
                                            entry_time_ns=a.created_at_ns, entry_game_time_sec=ds_res.game_time_sec,
                                            event_type="DSWING", expected_move=0.0, fair_price=ds_res.series_fair,
                                            trader_kind="dswing", exit_horizon_sec=None, signal_id=ds_res.signal_id,
                                            backed_direction=ds_res.direction))
                                        mode = "LIVE" if ENABLE_REAL_LIVE_TRADING else "PAPER"
                                        print(f"DSWING {mode} ENTER {mapping.get('name')} {ds_res.side} ask={ds_res.ask:.3f} fair={ds_res.series_fair:.3f} edge={ds_res.edge:+.3f} status={a.order_status}")
                                    else:
                                        print(f"DSWING REJECT {mapping.get('name')} {ds_res.side} reason={a.reason_if_rejected}")

                        # --- Event Detectors ---
                        if EVENT_DETECTORS_ENABLED:
                            dota_events = event_detector.observe(game, mapping)
                        else:
                            dota_events = []
                        event_detected_ns = time.time_ns()
                        if dota_events:
                            event_logger.log_events(dota_events)
                            # Final model: score same-direction event clusters once,
                            # log every cluster decision, then enter only the best passing
                            # candidate instead of the first arbitrary direction.
                            # 2026-06-01 — ALL-EVENTS-INDEPENDENT mode: one cluster
                            # PER EVENT (was: grouped by direction). Each event is
                            # evaluated and attributed on its own; the per-tick
                            # _best_signal_candidate still serializes to one trade
                            # per match per tick (prevents buying both sides at once),
                            # but across ticks every event type can trade.
                            cluster_list = [[evt] for evt in dota_events if (evt.direction or "")]

                            candidates = []
                            for cluster_events in cluster_list:
                                event_direction = cluster_events[0].direction or ""
                                if not event_direction:
                                    continue

                                signal_eval_start_ns = time.time_ns()
                                _fsd = event_detector.get_first_swing_direction(
                                    str(game.get("match_id") or game.get("lobby_id") or "")
                                )
                                if _fsd is not None:
                                    game = dict(game)
                                    game["first_swing_direction"] = _fsd
                                signal = signal_engine.evaluate_cluster(
                                    events=cluster_events,
                                    game=game,
                                    mapping=mapping,
                                    yes_book=yes_book,
                                    no_book=no_book,
                                    require_primary=not (LIVE_TRADING and ALLOW_CONFIRMATION_ONLY_LIVE_TRADES),
                                )
                                signal_evaluated_ns = time.time_ns()

                                # OPTIMIZATION: If event-only signal already hit a hard skip (staleness),
                                # bypass the slow ML/Nowcast blocks to keep detection latency low for other matches.
                                hard_skip = (signal.get("decision") == "skip" and signal.get("reason") in ("steam_stale", "source_update_stale"))

                                if not hard_skip:
                                    # Attach RealtimeStats context to signal dict.
                                    rt_fresh = game.get("realtime_context_status") == "fresh"
                                    signal["realtime_context_status"] = game.get("realtime_context_status")
                                    signal["realtime_stats_age_sec"] = game.get("realtime_stats_age_sec")
                                    signal["game_time_lag_sec"] = game.get("game_time_lag_sec")
                                    if rt_fresh:
                                        for field in ("aegis_team", "radiant_dead_count", "dire_dead_count",
                                                     "radiant_core_dead_count", "dire_core_dead_count",
                                                     "radiant_max_respawn", "dire_max_respawn",
                                                     "radiant_top3_nw", "dire_top3_nw"):
                                            signal[field] = game.get(field)
                                        signal["realtime_derived_events"] = game.get("realtime_derived_events", [])
                                    else:
                                        signal["realtime_derived_events"] = []

                                    # ML prediction for slow_model_fair
                                    slow_model_fair = None
                                    if model_bundle is not None:
                                        try:
                                            feat_row = build_feature_row(game)
                                            pred = model_bundle.predict_radiant(feat_row)
                                            p_rad = pred.get("radiant_fair_probability")
                                            if p_rad is not None:
                                                slow_model_fair = p_rad if event_direction == "radiant" else (1.0 - p_rad)
                                        except Exception as e:
                                            print(f"ML prediction error: {e}")

                                    hybrid_context = _hybrid_context(game)
                                    lag = _hybrid_delay_seconds(game)
                                    nowcast = compute_hybrid_nowcast(
                                        latest_realtime_features=hybrid_context,
                                        latest_toplive_snapshot=game,
                                        toplive_event_cluster=cluster_events,
                                        source_delay_metrics={"game_time_lag_sec": lag},
                                        slow_model_fair=slow_model_fair,
                                        event_only_fair=signal.get("fair_price"),
                                        game_time_sec=game.get("game_time_sec"),
                                        event_direction=event_direction,
                                    )
                                    nowcast_data = nowcast.to_dict()
                                    signal.update(nowcast_data)

                                    if nowcast.hybrid_fair is not None:
                                        hybrid_signal = signal_engine.evaluate_cluster(
                                            events=cluster_events,
                                            game=game,
                                            mapping=mapping,
                                            yes_book=yes_book,
                                            no_book=no_book,
                                            require_primary=not (LIVE_TRADING and ALLOW_CONFIRMATION_ONLY_LIVE_TRADES),
                                            fair_price_override=nowcast.hybrid_fair,
                                            fair_source="hybrid",
                                        )
                                        # Preserve context and metadata in the hybrid signal dict
                                        for key in (
                                            "realtime_context_status", "realtime_stats_age_sec", "game_time_lag_sec",
                                            "aegis_team", "radiant_dead_count", "dire_dead_count",
                                            "radiant_core_dead_count", "dire_core_dead_count",
                                            "radiant_max_respawn", "dire_max_respawn",
                                            "radiant_top3_nw", "dire_top3_nw", "realtime_derived_events",
                                        ):
                                            if key in signal:
                                                hybrid_signal[key] = signal[key]
                                        hybrid_signal.update(nowcast_data)
                                        signal = hybrid_signal

                                signal["mapping_confidence"] = game.get("mapping_confidence")
                                signal["mapping_errors"] = game.get("mapping_errors")
                                signal["team_id_match"] = game.get("team_id_match")
                                signal["market_game_number_match"] = game.get("market_game_number_match")
                                signal["duplicate_match_id_error"] = game.get("duplicate_match_id_error")

                                if signal.get("reason") == "no_primary_event":
                                    shadow_signal = signal_engine.evaluate_cluster(
                                        events=cluster_events,
                                        game=game,
                                        mapping=mapping,
                                        yes_book=yes_book,
                                        no_book=no_book,
                                        require_primary=False,
                                    )
                                    if shadow_signal.get("decision") == "paper_buy_yes":
                                        shadow_signal = dict(shadow_signal)
                                        shadow_signal["decision"] = "skip"
                                        shadow_signal["reason"] = "shadow_no_primary"
                                        signal = shadow_signal

                                # ── Stale-book rescue for Tier A/B events ──
                                # When a Tier A/B signal is blocked by a stale/missing local book,
                                # fetch a fresh orderbook via REST and re-evaluate.
                                _rescue_tok_id = signal.get("token_id", "")
                                _rescue_skip = signal.get("reason", "")
                                _rescue_evt = signal.get("event_type") or ""
                                _rescue_tier = signal.get("event_tier") or ""
                                if (
                                    _rescue_skip in {"book_stale", "missing_book"}
                                    and _rescue_evt in (TIER_A_EVENTS | TIER_B_EVENTS)
                                    and http_session is not None
                                    and _rescue_tok_id
                                ):
                                    _local_book = book_store.get(_rescue_tok_id) or {}
                                    _local_bid = _local_book.get("best_bid")
                                    _local_ask = _local_book.get("best_ask")
                                    _local_spread = None
                                    if _local_bid is not None and _local_ask is not None:
                                        try:
                                            _local_spread = round(float(_local_ask) - float(_local_bid), 4)
                                        except (TypeError, ValueError):
                                            _local_spread = None
                                    _local_ask_size = _local_book.get("ask_size")
                                    _local_book_age = signal.get("book_age_ms")

                                    _rescue_row = {
                                        "match_id": str(game.get("match_id") or ""),
                                        "event_type": _rescue_evt,
                                        "event_tier": _rescue_tier,
                                        "event_direction": event_direction,
                                        "token_id": _rescue_tok_id,
                                        "local_book_age_ms": _local_book_age,
                                        "local_bid": _local_bid,
                                        "local_ask": _local_ask,
                                        "local_spread": _local_spread,
                                        "local_ask_size": _local_ask_size,
                                    }

                                    try:
                                        _fresh_book = await fetch_fresh_book(http_session, _rescue_tok_id, timeout_ms=2000)
                                    except Exception:
                                        _fresh_book = None

                                    if _fresh_book and _fresh_book.get("best_ask") is not None:
                                        _stored_fresh_book = book_store.update_direct(
                                            _rescue_tok_id,
                                            best_bid=_fresh_book.get("best_bid"),
                                            best_ask=_fresh_book.get("best_ask"),
                                            bid_size=_fresh_book.get("bid_size"),
                                            ask_size=_fresh_book.get("ask_size"),
                                            raw=_fresh_book.get("raw"),
                                        )
                                        if _fresh_book.get("best_bid") is not None and _fresh_book.get("best_ask") is not None:
                                            _fresh_mid = (float(_fresh_book["best_bid"]) + float(_fresh_book["best_ask"])) / 2.0
                                            signal_engine.record_price(_rescue_tok_id, _fresh_mid, game.get("game_time_sec"))
                                        _rescue_row["refresh_request_start_ns"] = _fresh_book.get("request_start_ns")
                                        _rescue_row["refresh_response_ns"] = _fresh_book.get("received_at_ns")
                                        _rescue_row["refresh_latency_ms"] = round(_fresh_book.get("refresh_latency_ns", 0) / 1_000_000, 1)
                                        _rescue_row["fresh_bid"] = _fresh_book.get("best_bid")
                                        _rescue_row["fresh_ask"] = _fresh_book.get("best_ask")
                                        _rescue_row["fresh_spread"] = _fresh_book.get("spread")
                                        _rescue_row["fresh_ask_size"] = _fresh_book.get("ask_size")
                                        _fresh_ts = _fresh_book.get("received_at_ns")
                                        if _fresh_ts:
                                            _rescue_row["fresh_book_age_ms_if_available"] = int((time.time_ns() - _fresh_ts) / 1_000_000)
                                        else:
                                            _rescue_row["fresh_book_age_ms_if_available"] = None

                                        if _local_ask is not None and _fresh_book.get("best_ask") is not None:
                                            try:
                                                _rescue_row["local_to_fresh_ask_change"] = round(float(_fresh_book["best_ask"]) - float(_local_ask), 4)
                                            except (TypeError, ValueError):
                                                _rescue_row["local_to_fresh_ask_change"] = None

                                        # Re-evaluate with fresh book substituted
                                        _fresh_yes_book = yes_book
                                        _fresh_no_book = no_book
                                        _fresh_side = signal.get("side", "")
                                        if _fresh_side == "YES":
                                            if _rescue_tok_id == mapping.get("yes_token_id"):
                                                _fresh_yes_book = _stored_fresh_book
                                            else:
                                                _fresh_no_book = _stored_fresh_book
                                        else:
                                            if _rescue_tok_id == mapping.get("no_token_id"):
                                                _fresh_no_book = _stored_fresh_book
                                            else:
                                                _fresh_yes_book = _stored_fresh_book

                                        _fresh_signal = signal_engine.evaluate_cluster(
                                            events=cluster_events,
                                            game=game,
                                            mapping=mapping,
                                            yes_book=_fresh_yes_book,
                                            no_book=_fresh_no_book,
                                            require_primary=not (LIVE_TRADING and ALLOW_CONFIRMATION_ONLY_LIVE_TRADES),
                                            fair_price_override=signal.get("hybrid_fair"),
                                            fair_source="hybrid_rescue" if signal.get("hybrid_fair") is not None else None,
                                        )
                                        _fresh_signal.update(nowcast_data)
                                        for key in (
                                            "realtime_context_status", "realtime_stats_age_sec", "game_time_lag_sec",
                                            "aegis_team", "radiant_dead_count", "dire_dead_count",
                                            "radiant_core_dead_count", "dire_core_dead_count",
                                            "radiant_max_respawn", "dire_max_respawn",
                                            "radiant_top3_nw", "dire_top3_nw", "realtime_derived_events",
                                            "mapping_confidence", "mapping_errors", "team_id_match",
                                            "market_game_number_match", "duplicate_match_id_error",
                                        ):
                                            if key in signal:
                                                _fresh_signal[key] = signal[key]
                                        _rescue_row["fresh_executable_edge"] = _fresh_signal.get("executable_edge")
                                        _rescue_row["fresh_remaining_move"] = _fresh_signal.get("remaining_move")
                                        _rescue_row["fresh_decision"] = _fresh_signal.get("decision")
                                        _rescue_row["fresh_skip_reason"] = _fresh_signal.get("reason")
                                        _rescue_row["fresh_fair_source"] = _fresh_signal.get("fair_source")
                                        _rescue_row["fresh_hybrid_fair"] = _fresh_signal.get("hybrid_fair")

                                        # Markouts are sampled asynchronously after logging the rescue row.
                                        # A background task will fill them in later.
                                        _rescue_row["markout_3s"] = None
                                        _rescue_row["markout_10s"] = None
                                        _rescue_row["markout_30s"] = None
                                        signal = _fresh_signal
                                        yes_book = _fresh_yes_book
                                        no_book = _fresh_no_book
                                    else:
                                        _rescue_row["refresh_latency_ms"] = None
                                        _rescue_row["fresh_bid"] = None
                                        _rescue_row["fresh_ask"] = None
                                        _rescue_row["fresh_spread"] = None
                                        _rescue_row["fresh_ask_size"] = None
                                        _rescue_row["fresh_book_age_ms_if_available"] = None
                                        _rescue_row["local_to_fresh_ask_change"] = None
                                        _rescue_row["fresh_executable_edge"] = None
                                        _rescue_row["fresh_remaining_move"] = None
                                        _rescue_row["fresh_decision"] = "rescue_failed"
                                        _rescue_row["fresh_skip_reason"] = "fresh_book_fetch_empty" if _fresh_book is None else "fresh_book_missing_ask"
                                        _rescue_row["markout_3s"] = None
                                        _rescue_row["markout_10s"] = None
                                        _rescue_row["markout_30s"] = None

                                    rescue_logger.log_rescue(_rescue_row)
                                    _rescue_lat = _rescue_row.get("refresh_latency_ms")
                                    _rescue_lat_str = f"{_rescue_lat:.0f}ms" if _rescue_lat is not None else "timeout"
                                    print(
                                        f"BOOK_RESCUE {_rescue_evt} tier={_rescue_tier} "
                                        f"local_age={_local_book_age}ms fresh_decision={_rescue_row.get('fresh_decision', '')} "
                                        f"fresh_reason={_rescue_row.get('fresh_skip_reason', '')} latency={_rescue_lat_str}"
                                    )

                                tok_id = signal.get("token_id", "")
                                tok_side = signal.get("side", "")
                                event_names = [evt.event_type for evt in cluster_events]

                                # Latency logging
                                selected_book = (yes_book if tok_id == mapping["yes_token_id"] else no_book) if tok_id else (yes_book or no_book)
                                latency_row = {
                                    "match_id": str(game.get("match_id") or ""),
                                    "market_name": mapping.get("name"),
                                    "event_type": signal.get("event_type") or "+".join(event_names),
                                    "cluster_event_types": signal.get("cluster_event_types") or "+".join(event_names),
                                    "event_direction": event_direction,
                                    "game_time_sec": game.get("game_time_sec"),
                                    "data_source": game.get("data_source"),
                                    "steam_received_at_ns": game.get("received_at_ns"),
                                    "steam_source_update_age_sec": game.get("source_update_age_sec"),
                                    "stream_delay_s": game.get("stream_delay_s"),
                                    "event_detected_ns": event_detected_ns,
                                    "signal_eval_start_ns": signal_eval_start_ns,
                                    "signal_evaluated_ns": signal_evaluated_ns,
                                    "token_id": tok_id,
                                    "side": tok_side,
                                    "book_received_at_ns": selected_book.get("received_at_ns") if selected_book else None,
                                    "book_age_at_signal_ms": signal.get("book_age_ms"),
                                    "best_bid": selected_book.get("best_bid") if selected_book else None,
                                    "best_ask": selected_book.get("best_ask") if selected_book else None,
                                    "spread": signal.get("spread"),
                                    "ask_size": signal.get("ask_size"),
                                    "decision": signal.get("decision"),
                                    "skip_reason": signal.get("reason"),
                                    "fair_price": signal.get("fair_price"),
                                    "executable_price": signal.get("executable_price"),
                                    "executable_edge": signal.get("executable_edge"),
                                    "remaining_move": signal.get("remaining_move"),
                                    "fair_source": signal.get("fair_source"),
                                    "required_edge": signal.get("required_edge"),
                                    "lag": signal.get("lag"),
                                    "mapping_confidence": game.get("mapping_confidence"),
                                    "mapping_errors": game.get("mapping_errors"),
                                    "team_id_match": game.get("team_id_match"),
                                    "market_game_number_match": game.get("market_game_number_match"),
                                    "duplicate_match_id_error": game.get("duplicate_match_id_error"),
                                    "slow_model_fair": signal.get("slow_model_fair"),
                                    "fast_event_adjustment": signal.get("fast_event_adjustment"),
                                    "hybrid_fair": signal.get("hybrid_fair"),
                                    "hybrid_confidence": signal.get("hybrid_confidence"),
                                    "uncertainty_penalty": signal.get("uncertainty_penalty"),
                                }
                                latency_logger.log_latency(latency_row)

                                signal_logger.log_signal(
                                    game, mapping, signal,
                                    event_type=signal.get("cluster_event_types") or "+".join(event_names),
                                    event_direction=event_direction,
                                    severity=signal.get("severity") or "+".join(evt.severity for evt in cluster_events),
                                    token_id=tok_id,
                                    side=tok_side,
                                )
                                if tok_id and (
                                    signal.get("event_is_primary") is True
                                    or str(signal.get("event_is_primary")).lower() == "true"
                                    or signal.get("event_tier") in {"A", "B"}
                                ):
                                    ref_bid = selected_book.get("best_bid") if selected_book else signal.get("bid")
                                    ref_ask = selected_book.get("best_ask") if selected_book else signal.get("ask")
                                    reference_price = ref_ask or signal.get("executable_price")
                                    if reference_price is None:
                                        reference_price = _book_mid(selected_book)
                                    asyncio.create_task(_log_signal_markouts({
                                        "signal_timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                                        "match_id": str(game.get("match_id") or ""),
                                        "market_name": mapping.get("name"),
                                        "event_type": signal.get("event_type"),
                                        "event_tier": signal.get("event_tier"),
                                        "event_is_primary": signal.get("event_is_primary"),
                                        "event_direction": event_direction,
                                        "token_id": tok_id,
                                        "side": tok_side,
                                        "decision": signal.get("decision"),
                                        "skip_reason": signal.get("reason"),
                                        "reference_price": reference_price,
                                        "reference_bid": ref_bid,
                                        "reference_ask": ref_ask,
                                        "fair_price": signal.get("fair_price"),
                                        "hybrid_fair": signal.get("hybrid_fair"),
                                        "executable_edge": signal.get("executable_edge"),
                                    }, tok_id, book_store, signal_markout_logger))

                                cp = _exit_adverse_position_for_signal(signal, mapping, trader, book_store)
                                if cp:
                                    position_logger.log_exit(cp)
                                    print(
                                        f"ADVERSE EXIT {mapping['name']} {cp.side} "
                                        f"pnl=${cp.pnl_usd:+.2f} hold={cp.hold_sec:.0f}s"
                                    )
                                
                                if check_live_exits_fn and signal.get("event_is_primary"):
                                    favored_token_id = signal.get("token_id")
                                    if favored_token_id:
                                        yes_token = mapping.get("yes_token_id")
                                        no_token = mapping.get("no_token_id")
                                        opposing_token = no_token if favored_token_id == yes_token else yes_token
                                        asyncio.create_task(check_live_exits_fn(adverse_token_ids={opposing_token}))

                                if ENABLE_MATCH_WINNER_RESEARCH and mapping.get("market_type") == "MATCH_WINNER":
                                    # Task 4: Match Winner research mode sidecar
                                    try:
                                        m_yes_book = yes_book or {}
                                        m_no_book = no_book or {}
                                        match_bid = m_yes_book.get("best_bid") if tok_side == "YES" else m_no_book.get("best_bid")
                                        match_ask = m_yes_book.get("best_ask") if tok_side == "YES" else m_no_book.get("best_ask")

                                        # Find the corresponding Map Winner mapping to get Map prices
                                        map_m = next((m for m in mappings if str(m.get("dota_match_id")) == str(mapping.get("dota_match_id")) and m.get("market_type") == "MAP_WINNER"), None)

                                        row = {
                                            "timestamp_ns": time.time_ns(),
                                            "match_id": str(game.get("match_id") or ""),
                                            "event_type": signal.get("event_type") or "+".join(event_names),
                                            "event_direction": event_direction,
                                            "match_token_id": tok_id,
                                            "match_bid": match_bid,
                                            "match_ask": match_ask,
                                            "match_book_age_ms": signal.get("book_age_ms"),
                                            "match_fair_after": signal.get("fair_price"),
                                            "match_edge": signal.get("executable_edge"),
                                            "decision": "skip",
                                            "skip_reason": "research_mode_match_winner",
                                        }

                                        # Try to fill in map-based fair values if we have a map mapping
                                        if map_m:
                                            map_yes_tok = map_m.get("yes_token_id")
                                            map_no_tok = map_m.get("no_token_id")
                                            row["map_token_id"] = map_yes_tok if tok_side == "YES" else map_no_tok

                                            m_yes_b = book_store.get(map_yes_tok) or {}
                                            m_no_b = book_store.get(map_no_tok) or {}
                                            map_book = m_yes_b if tok_side == "YES" else m_no_b
                                            row["map_bid"] = map_book.get("best_bid")
                                            row["map_ask"] = map_book.get("best_ask")
                                            row["map_book_age_ms"] = age_ms(map_book.get("received_at_ns"))

                                            # Anchor for map before event
                                            map_anchor = signal_engine._price_n_seconds_ago(row["map_token_id"], PRICE_LOOKBACK_SEC)
                                            if map_anchor is None:
                                                map_anchor = signal_engine._pregame_price.get(row["map_token_id"])

                                            if map_anchor is not None:
                                                row["current_map_p_before"] = map_anchor
                                                expected_move = signal.get("expected_move") or 0.0
                                                row["current_map_p_after"] = apply_probability_move(map_anchor, expected_move)

                                                # Compute match_fair_before
                                                p_next_yes = float(mapping.get("p_next_yes") or row["current_map_p_after"])
                                                row["p_next_yes"] = p_next_yes
                                                row["p_next_source"] = "mapping" if mapping.get("p_next_yes") else "map_fair"
                                                row["neutral_p_next_yes"] = 0.5

                                                series_score_yes = int(game.get("series_score_yes", 0))
                                                series_score_no = int(game.get("series_score_no", 0))
                                                current_game_number = int(game.get("current_game_number") or game.get("game_number_in_series") or 1)
                                                series_type_val = int(mapping.get("series_type") or 1)

                                                try:
                                                    p_map_before = map_anchor if tok_side == "YES" else 1.0 - map_anchor
                                                    p_next_yes_val = p_next_yes if tok_side == "YES" else 1.0 - p_next_yes

                                                    row["match_fair_before"] = compute_bo3_match_p(
                                                        p_current_map_yes=max(0.01, min(0.99, p_map_before)),
                                                        p_next_yes=max(0.01, min(0.99, p_next_yes_val)),
                                                        series_score_yes=series_score_yes,
                                                        series_score_no=series_score_no,
                                                        current_game_number=current_game_number,
                                                        series_type=series_type_val,
                                                    )
                                                    if tok_side == "NO":
                                                        row["match_fair_before"] = 1.0 - row["match_fair_before"]

                                                    if row["match_fair_before"] is not None and row["match_fair_after"] is not None:
                                                        row["match_fair_delta"] = row["match_fair_after"] - row["match_fair_before"]
                                                except Exception:
                                                    pass

                                        if match_winner_logger:
                                            match_winner_logger.log_match_signal(row)
                                    except Exception as e:
                                        print(f"Error in MATCH_WINNER sidecar: {e}")
                                        traceback.print_exc()
                                    finally:
                                        # Only force skip if it's NOT a decider Game 3 proxy
                                        if not is_game3_match_proxy(mapping):
                                            signal["decision"] = "skip"
                                            signal["reason"] = "research_mode_match_winner"

                                # Shadow trade logging for MAP_WINNER, BO1 MATCH_WINNER (the
                                # whole series IS one game), and Game3 MATCH_WINNER proxies.
                                # Without the BO1 case shadow_trades.csv stayed empty for all
                                if ENABLE_EVENT_ENTRY_STRATEGY and signal["decision"] == "paper_buy_yes":
                                    candidates.append({
                                        "signal": signal,
                                        "direction": event_direction,
                                        "events": cluster_events,
                                        "latency_row": latency_row,
                                    })

                            best = _best_signal_candidate(candidates)
                            if best:
                                signal = best["signal"]
                                cluster_events = best["events"]
                                event_direction = best["direction"]
                                latency_row = best["latency_row"]
                                tok_id = signal.get("token_id", "")
                                tok_side = signal.get("side", "")
                                event_names = [evt.event_type for evt in cluster_events]

                                print(
                                    f"EVENT_CLUSTER {signal.get('cluster_event_types') or '+'.join(event_names)} "
                                    f"dir={event_direction} t={game_time}s "
                                    f"edge={signal.get('executable_edge')}"
                                )

                                opposing_tok = (
                                    mapping["no_token_id"] if tok_id == mapping["yes_token_id"]
                                    else mapping["yes_token_id"]
                                )

                                # Block entry if we already hold the opposing side or are stacking
                                # the same token beyond 1 open position (avoids self-hedging).
                                if live_position_store:
                                    active_states = {"OPEN", "EXITING", "PENDING_ENTRY", "PENDING_EXIT_GTC"}
                                    active_toks = {
                                        p.token_id for p in live_position_store.positions.values()
                                        if p.state in active_states
                                    }
                                    if opposing_tok and str(opposing_tok) in active_toks:
                                        print(f"LIVE_SKIP opposing_position_exists: {mapping.get('name')} {tok_side}")
                                        if live_logger:
                                            live_logger.log(signal, mapping, game, skip_reason="opposing_position_exists")
                                        continue

                                if live_executor and live_logger:
                                    attempt = await live_executor.try_buy(
                                        signal=signal,
                                        mapping=mapping,
                                        game=game,
                                        book_store=book_store,
                                    )
                                    # Log live latency result
                                    live_latency_row = dict(latency_row)
                                    live_latency_row.update({
                                        "decision": "live_attempt_result",
                                        "live_submit_start_ns": attempt.submit_start_ns,
                                        "live_response_received_ns": attempt.response_received_ns,
                                        "live_submit_latency_ms": attempt.submit_latency_ms,
                                        "live_order_status": attempt.order_status,
                                        "live_reject_reason": attempt.reason_if_rejected,
                                        "live_submitted_size_usd": attempt.submitted_size_usd,
                                        "live_filled_size_usd": attempt.filled_size_usd,
                                        "live_avg_fill_price": attempt.avg_fill_price,
                                    })
                                    latency_logger.log_latency(live_latency_row)

                                    asyncio.create_task(_log_live_attempt_with_markouts(attempt, book_store, live_logger))
                                    print(
                                        f"LIVE_ATTEMPT {mapping['name']} {tok_side} "
                                        f"status={attempt.order_status} "
                                        f"size=${attempt.submitted_size_usd:.2f} "
                                        f"filled=${attempt.filled_size_usd:.2f} "
                                        f"cap={attempt.price_cap} "
                                        f"reason={attempt.reason_if_rejected}"
                                    )
                                    if attempt.submitted_size_usd > 0:
                                        signal_engine.commit_signal(signal)
                                    
                                    if attempt.filled_size_usd > 0 and attempt.avg_fill_price and live_position_store:
                                        shares = attempt.filled_size_usd / attempt.avg_fill_price
                                        pos_id = f"{attempt.match_id}:{attempt.token_id}:{attempt.created_at_ns}"
                                        live_pos = LivePosition(
                                            position_id=pos_id,
                                            state="OPEN",
                                            token_id=attempt.token_id,
                                            opposing_token_id=opposing_tok,
                                            match_id=attempt.match_id,
                                            market_name=mapping.get("name"),
                                            side=tok_side,
                                            entry_price=attempt.avg_fill_price,
                                            shares=shares,
                                            cost_usd=attempt.filled_size_usd,
                                            entry_time_ns=attempt.created_at_ns,
                                            entry_game_time_sec=game.get("game_time_sec"),
                                            event_type=signal.get("event_type") or "",
                                            expected_move=signal.get("expected_move") or 0.0,
                                            fair_price=signal.get("fair_price") or 0.0,
                                        )
                                        live_position_store.add(live_pos)
                                    elif attempt.order_status in ("delayed", "live") and attempt.order_id and live_position_store:
                                        # Track as pending entry — assume fill at price_cap for shares estimation
                                        shares = attempt.submitted_size_usd / attempt.price_cap
                                        pos_id = f"{attempt.match_id}:{attempt.token_id}:{attempt.created_at_ns}"
                                        live_pos = LivePosition(
                                            position_id=pos_id,
                                            state="PENDING_ENTRY",
                                            token_id=attempt.token_id,
                                            opposing_token_id=opposing_tok,
                                            match_id=attempt.match_id,
                                            market_name=mapping.get("name"),
                                            side=tok_side,
                                            entry_price=attempt.price_cap,
                                            shares=shares,
                                            cost_usd=attempt.submitted_size_usd,
                                            entry_time_ns=attempt.created_at_ns,
                                            entry_game_time_sec=game.get("game_time_sec"),
                                            event_type=signal.get("event_type") or "",
                                            expected_move=signal.get("expected_move") or 0.0,
                                            fair_price=signal.get("fair_price") or 0.0,
                                            pending_entry_order_id=attempt.order_id,
                                        )
                                        live_position_store.add(live_pos)
                                        print(f"LIVE ENTRY PENDING: {mapping['name']} {tok_side} order={attempt.order_id}")
                                else:
                                    paper_attempt_ns = time.time_ns()
                                    if PAPER_EXECUTION_DELAY_MS > 0:
                                        await asyncio.sleep(PAPER_EXECUTION_DELAY_MS / 1000.0)

                                    pos, reason = trader.enter(
                                        signal=signal,
                                        token_id=tok_id,
                                        side=tok_side,
                                        book_store=book_store,
                                        match_id=str(game.get("match_id") or ""),
                                        market_name=mapping.get("name"),
                                        opposing_token_id=opposing_tok,
                                    )
                                    paper_fill_ns = time.time_ns()

                                    # Log paper latency result
                                    paper_latency_row = dict(latency_row)
                                    paper_latency_row.update({
                                        "decision": "paper_entry_result",
                                        "paper_delay_ms": PAPER_EXECUTION_DELAY_MS,
                                        "paper_attempt_ns": paper_attempt_ns,
                                        "paper_fill_ns": paper_fill_ns,
                                        "paper_entry_result": "filled" if pos else "skipped",
                                        "paper_fill_price": pos.entry_price if pos else None,
                                        "skip_reason": reason if not pos else None,
                                    })
                                    latency_logger.log_latency(paper_latency_row)

                                    if pos:
                                        signal_engine.commit_signal(signal)
                                        position_logger.log_entry(pos)
                                        print(
                                            f"ENTER {mapping['name']} {tok_side} "
                                            f"price={pos.entry_price:.4f} "
                                            f"shares={pos.shares:.2f} "
                                            f"cost=${pos.cost_usd:.2f} "
                                            f"lag={pos.lag:.3f} "
                                            f"exp={pos.expected_move:.3f} "
                                            f"event={signal.get('event_type')}"
                                        )
                                    else:
                                        print(f"SKIP ENTRY: {reason}")

                # 2. Tick-level model fair maintenance, with optional ML-only entries.
                # This runs even when ML_STRATEGY_ENABLED=false so event/hybrid
                # positions can exit against updated model value.
                if model_bundle:
                    for game in active_games:
                        # Skip if we already just processed events for this match in this poll
                        # (The hybrid nowcast already incorporated the ML fair for those)
                        # Actually, running ML check every tick is safer to catch gradual drifts.
                        
                        match_id = str(game.get("match_id") or "")
                        game_time = game.get("game_time_sec") or 0
                        if game_time < 300: # 5m guard
                            continue
                            
                        # Inject team win ratios from historical stats
                        r_id = str(game.get("radiant_team_id") or "")
                        d_id = str(game.get("dire_team_id") or "")
                        game["radiant_team_win_ratio"] = team_stats.get(r_id, 0.5)
                        game["dire_team_win_ratio"] = team_stats.get(d_id, 0.5)
                        
                        for mapping in mappings:
                            if str(mapping["dota_match_id"]) not in {match_id, str(game.get("lobby_id") or "")}:
                                continue
                            
                            yes_tok = mapping["yes_token_id"]
                            no_tok = mapping["no_token_id"]
                                
                            try:
                                feat_row = build_feature_row(game)
                                pred = model_bundle.predict_radiant(feat_row)
                                p_rad = pred.get("radiant_fair_probability")
                                if p_rad is None: continue
                                
                                yes_book = book_store.get(yes_tok)
                                if not yes_book or not yes_book.get("best_ask"): continue
                                yes_bid = yes_book.get("best_bid")
                                if yes_bid is None:
                                    continue
                                
                                yes_fair_direction = _yes_fair_from_radiant(mapping, game, p_rad)
                                if yes_fair_direction is None:
                                    continue
                                yes_fair, yes_direction = yes_fair_direction
                                trader.update_fair_value(yes_tok, yes_fair)
                                trader.update_fair_value(no_tok, 1.0 - yes_fair)

                                if not ML_STRATEGY_ENABLED:
                                    continue

                                # Only enter if we don't already have an open position in this market.
                                # Existing positions use the refreshed fair value in check_exits().
                                if yes_tok in trader.positions or no_tok in trader.positions:
                                    continue

                                # Evaluate the market YES token using the mapped Steam side.
                                mkt_price = float(yes_book["best_ask"])
                                spread = mkt_price - float(yes_bid)
                                ask_size = yes_book.get("ask_size")
                                if mkt_price <= 0.05 or mkt_price >= 0.95:
                                    continue
                                if spread > MAX_SPREAD:
                                    continue
                                if ask_size is not None and mkt_price * float(ask_size) < MIN_ASK_SIZE_USD:
                                    continue

                                edge = yes_fair - mkt_price
                                
                                if edge >= MIN_ML_EDGE:
                                    signal = {
                                        "event_type": "ML_ARBITRAGE",
                                        "event_direction": yes_direction,
                                        "side": "YES",
                                        "token_id": yes_tok,
                                        "fair_price": round(yes_fair, 4),
                                        "executable_price": mkt_price,
                                        "executable_edge": round(edge, 4),
                                        "remaining_move": round(edge, 4),
                                        "expected_move": round(edge, 4),
                                        "required_edge": MIN_ML_EDGE,
                                        "ask": mkt_price,
                                        "bid": float(yes_bid),
                                        "spread": round(spread, 4),
                                        "ask_size": ask_size,
                                        "decision": "paper_buy_yes",
                                        "reason": "ml_valuation_edge",
                                        "severity": "ML",
                                        "game_time_sec": game_time,
                                    }
                                    
                                    # Reuse paper entry logic
                                    paper_attempt_ns = time.time_ns()
                                    pos, reason = trader.enter(
                                        signal=signal,
                                        token_id=yes_tok,
                                        side="YES",
                                        book_store=book_store,
                                        match_id=match_id,
                                        market_name=mapping.get("name"),
                                        opposing_token_id=no_tok,
                                    )
                                    if pos:
                                        position_logger.log_entry(pos)
                                        print(f"ML_ENTER {mapping['name']} YES price={pos.entry_price:.4f} edge={edge:.4f}")

                            except Exception as e:
                                print(f"Tick-level ML error: {e}")

            except Exception as e:
                print("steam_loop error:", repr(e))
                traceback.print_exc()

            if _iter_debug == 1 or (now - _last_iter_print) < 0.1:
                print(f"DEBUG iter={_iter_debug} END_OF_ITER (about to sleep {STEAM_POLL_SECONDS}s)")
            # 2026-06-01 — Watchdog heartbeat: touch a file every poll iteration so
            # supervisor.py can detect a HANG (process alive but loop stuck — the
            # zombie failure mode where PID is live but no work happens). Liveness
            # by PID is insufficient; this proves the loop is actually turning.
            try:
                with open("logs/heartbeat", "w") as _hb:
                    _hb.write(str(time.time()))
            except Exception:
                pass
            await asyncio.sleep(STEAM_POLL_SECONDS)


def _write_heartbeat() -> None:
    """Write the supervisor watchdog heartbeat (epoch seconds). Shared by the
    startup heartbeat task and steam_loop."""
    try:
        with open("logs/heartbeat", "w") as _hb:
            _hb.write(str(time.time()))
    except Exception:
        pass


async def _startup_heartbeat_loop():
    """Keep the watchdog heartbeat fresh during the multi-minute async startup
    (reconcile + model load) that runs before steam_loop starts writing its own.
    In live mode the per-token balance reconcile overruns supervisor.py's startup
    grace; without this the bot is killed before booting → infinite restart loop.
    Cancelled once steam_loop takes over so real hangs stay detectable."""
    while True:
        _write_heartbeat()
        await asyncio.sleep(5)


async def main():
    if not _acquire_single_instance_lock():
        print("Another paper bot instance is already running; refusing to start.")
        return
    _write_heartbeat()
    _startup_hb_task = asyncio.create_task(_startup_heartbeat_loop())
    print(
        f"Runtime config: LIVE_TRADING={LIVE_TRADING} MODE={os.getenv('MODE', 'paper')} "
        f"ML_STRATEGY_ENABLED={ML_STRATEGY_ENABLED}"
    )

    # Initial sync: try to link any already-live Steam games before starting
    print("Running initial Steam market sync...")
    try:
        async with aiohttp.ClientSession() as session:
            games = await fetch_all_live_games(session)
        mdata = load_markets()
        updates = sync_markets_to_games(mdata.setdefault("markets", []), games)
        if updates:
            write_markets(mdata)
            for u in updates:
                print(f"  linked {u['market_name']} → {u['dota_match_id']}")
        else:
            print("  no live games matched markets.yaml right now (will retry every 60s)")
    except Exception as e:
        print(f"  initial sync error (non-fatal): {e}")

    mappings, errors = load_valid_mappings()
    _mapping_error_limit = int(os.getenv("STARTUP_MAPPING_ERROR_LOG_LIMIT", "25"))
    for err in errors[:_mapping_error_limit]:
        print(f"Skipping mapping #{err.index} ({err.name or 'unnamed'}): {err.reason}")
    if len(errors) > _mapping_error_limit:
        print(f"Skipping {len(errors) - _mapping_error_limit} additional invalid mapping(s); set STARTUP_MAPPING_ERROR_LOG_LIMIT to print more.")

    # Step 1: Filter to active strategy scope
    mappings = [
        m for m in mappings
        if is_active_strategy_mapping(
            m,
            enable_match_winner_game3_proxy=ENABLE_MATCH_WINNER_GAME3_PROXY,
            enable_match_winner_research=ENABLE_MATCH_WINNER_RESEARCH,
            enable_match_winner_trading=ENABLE_MATCH_WINNER_TRADING,
        )
    ]
    # 2026-05-27: drop mappings for known game_over matches (seeded from
    # raw_snapshots.csv at startup). Prevents the WS from subscribing to
    # settled-market tokens that produce no updates and trigger heartbeat
    # reconnect loops.
    if _PERSISTENT_GAME_OVER_MATCH_IDS:
        _before = len(mappings)
        mappings = [m for m in mappings
                    if str(m.get("dota_match_id") or "") not in _PERSISTENT_GAME_OVER_MATCH_IDS]
        if _before != len(mappings):
            print(f"STARTUP_WS_FILTER dropped {_before - len(mappings)} mappings for game_over matches")

    if not mappings:
        print("No active mappings yet — bot will keep checking every 60s for live games.")

    store = BookStore()
    trader = PaperTrader()
    signal_logger = SignalLogger()
    event_detector = EventDetector()
    signal_engine = EventSignalEngine()
    event_logger = DotaEventLogger()
    book_logger = BookEventLogger()
    position_logger = PositionLogger()
    snapshot_logger = RawSnapshotLogger()
    value_engine: ValueEngine | None = None
    value_logger: ValueAttemptLogger | None = None
    if VALUE_ENGINE_ENABLED:
        value_logger = ValueAttemptLogger()
        value_engine = ValueEngine()
        print(f"VALUE_ENGINE mode ON")
    dswing_engine: DecisiveSwingEngine | None = None
    dswing_logger: DSwingAttemptLogger | None = None
    if DSWING_ENABLED or DSWING_SHADOW_ENABLED:
        dswing_engine = DecisiveSwingEngine()
        dswing_logger = DSwingAttemptLogger()
        mode = "paper" if DSWING_ENABLED and not ENABLE_REAL_LIVE_TRADING else ("armed" if DSWING_ENABLED else "shadow")
        print(f"DECISIVE_SWING {mode} mode ON")
    latency_logger = LatencyLogger()
    rich_context_logger = RichContextLogger()
    source_delay_logger = SourceDelayLogger()

    run_live_infra = LIVE_TRADING
    
    if run_live_infra:
        attempt_csv = LIVE_ATTEMPTS_CSV_PATH if ENABLE_REAL_LIVE_TRADING else PAPER_ATTEMPTS_CSV_PATH
        exit_csv = "logs/live_exits.csv" if ENABLE_REAL_LIVE_TRADING else PAPER_EXITS_CSV_PATH
        pos_json = "logs/live_positions.json" if ENABLE_REAL_LIVE_TRADING else PAPER_POSITIONS_PATH
        
        live_logger = LiveAttemptLogger(filename=attempt_csv)
        live_executor = LiveExecutor()
        live_executor.set_delayed_resolution_callback(
            lambda attempt: live_logger.log_attempt(attempt, phase="resolution")
        )
        live_position_store = LivePositionStore(path=pos_json)
        live_exit_executor = LiveExitExecutor()
        live_exit_logger = LiveExitLogger(filename=exit_csv)
        try:
            from config import CODE_VERSION as _cv
            live_exit_logger.log_startup_heartbeat(code_version=_cv)
        except Exception:
            live_exit_logger.log_startup_heartbeat()
    else:
        live_logger = None
        live_executor = None
        live_position_store = None
        live_exit_executor = None
        live_exit_logger = None

    rescue_logger = BookRefreshRescueLogger()
    match_winner_logger = MatchWinnerSignalLogger(log_dir="logs") if ENABLE_MATCH_WINNER_RESEARCH else None
    signal_markout_logger = SignalMarkoutLogger()
    # 2026-05-30 — shadow logger disabled. Was redundant with signals.csv
    # (which already logs every decision + reason). The 4 would_pnl markout
    # columns it added were misleading: markouts are negative on every event
    # because the bot detects after the market moves, but the actual strategy
    # is hold-to-settle. Use signals.csv for decisions and paper_trades.csv
    # for the trade ledger.
    shadow_logger = None
    book_move_detector = BookMoveDetector()
    book_move_logger = BookMoveLogger()
    disk_guard = DiskGuard()
    disk_status = disk_guard.check(force=True)
    if not disk_status.ok:
        print(
            "DISK_GUARD HALT_NEW_ORDERS "
            f"path={disk_status.path} free_gb={disk_status.free_gb:.2f} "
            f"min_gb={disk_status.min_free_gb:.2f}"
        )
    # Dedup: track last execution attempt time per exec_tok_id to prevent duplicate orders
    # when both YES-up and NO-down signals fire simultaneously for the same market.
    _book_move_last_exec: dict[str, float] = {}
    _BOOK_MOVE_EXEC_COOLDOWN_SEC = 60.0

    # Pending book moves awaiting Steam corroboration
    _pending_book_moves: list[dict] = []

    # Shared mutable dict: match_id -> latest Steam game snapshot
    # Written by steam_loop, read by _on_book_update (same event loop thread — no locking needed)
    _last_steam_games: dict[str, dict] = {}
    # match_id -> last time it was seen live; lets proactive_refresh_loop keep
    # refreshing books through brief GetTopLive flickers (draft / gt=0 games).
    _match_last_live: dict[str, float] = {}
    REFRESH_GRACE_SEC = 90.0

    # Collect all background CSV loggers for graceful flush on shutdown
    loggers = [
        signal_logger,
        event_logger,
        book_logger,
        position_logger,
        snapshot_logger,
        latency_logger,
        rich_context_logger,
        source_delay_logger,
        rescue_logger,
        signal_markout_logger,
        shadow_logger,
        book_move_logger,
    ]
    if match_winner_logger:
        loggers.append(match_winner_logger)
    if live_logger:
        loggers.append(live_logger)
    if live_exit_logger:
        loggers.append(live_exit_logger)
    loggers = [logger for logger in loggers if logger is not None]
    restored_positions = trader.load_open_positions(position_logger.filename)
    if restored_positions:
        print(f"Restored {restored_positions} open paper position(s) from {position_logger.filename}")

    if live_exit_executor and ENABLE_REAL_LIVE_TRADING:
        print("Cancelling all open CLOB orders on startup...")
        await live_exit_executor.cancel_all_open_orders()
        # Seed game_over set from prior snapshots so the book refresher skips
        # already-finished matches immediately, not after a fresh game_over event.
        _PERSISTENT_GAME_OVER_MATCH_IDS.update(_load_game_over_match_ids_from_csv())
        print(f"STARTUP_GAME_OVER_SET size={len(_PERSISTENT_GAME_OVER_MATCH_IDS)}")
        if live_position_store:
            pre_summary = live_position_store.summarize()
            print(f"STARTUP_PRE_RECONCILE position_state_summary={pre_summary}")
            result = await reconcile_live_positions(
                client=live_exit_executor,
                store=live_position_store,
                mappings=mappings,
                live_executor=live_executor,
                live_exit_logger=live_exit_logger,
                book_store=store,
            )
            print(
                "STARTUP_RECONCILE "
                f"tokens={result.checked_tokens} active={result.active_after} "
                f"adjusted={result.adjusted_existing} reopened={result.reopened_missing} "
                f"closed={result.closed_stale} errors={result.balance_errors}"
            )
            
            async def periodic_reconciliation():
                while True:
                    await asyncio.sleep(600)  # Every 10 minutes
                    try:
                        res = await reconcile_live_positions(
                            client=live_exit_executor,
                            store=live_position_store,
                            mappings=mappings,
                            live_executor=live_executor,
                            live_exit_logger=live_exit_logger,
                            book_store=store,
                        )
                        if res.balance_errors > 0 or res.reopened_missing > 0 or res.closed_stale > 0 or res.adjusted_existing > 0:
                            print(
                                "RUNTIME_RECONCILE "
                                f"tokens={res.checked_tokens} active={res.active_after} "
                                f"adjusted={res.adjusted_existing} reopened={res.reopened_missing} "
                                f"closed={res.closed_stale} errors={res.balance_errors}"
                            )
                    except Exception as exc:
                        print(f"RUNTIME_RECONCILE_ERROR: {exc}")

            asyncio.create_task(periodic_reconciliation())

    model_bundle = None
    if os.path.exists(DOTA_FAIR_MODEL_PATH):
        print(f"Loading dota_fair model from {DOTA_FAIR_MODEL_PATH}...")
        try:
            model_bundle = load_bundle(DOTA_FAIR_MODEL_PATH)
            print(f"  model loaded (phases: {', '.join(model_bundle.models.keys())})")
        except Exception as e:
            print(f"  failed to load model: {e}")
    else:
        print(f"No model found at {DOTA_FAIR_MODEL_PATH} (skipping ML features)")

    asset_ids = []
    for m in mappings:
        asset_ids.extend([m["yes_token_id"], m["no_token_id"]])

    _exit_lock = asyncio.Lock()

    async def _check_live_exits(game_over_match_ids=None, adverse_token_ids=None):
        if not live_position_store or not live_exit_executor:
            return

        async with _exit_lock:
            game_over_match_ids = game_over_match_ids or set()
            adverse_token_ids = adverse_token_ids or set()

            # --- Poll pending entry orders ---
            for pos in live_position_store.pending_entry_positions():
                order_resp = await live_exit_executor.check_gtc_fill(pos.pending_entry_order_id)
                status = str(order_resp.get("status") or order_resp.get("orderStatus") or "").lower()

                if status in ("matched", "filled"):
                    filled_shares = _to_float(order_resp.get("filledShares") or order_resp.get("filled_shares"))
                    filled_usd = _to_float(
                        order_resp.get("filledSizeUsd")
                        or order_resp.get("filled_size_usd")
                        or order_resp.get("filledAmountUsd")
                        or order_resp.get("filled_amount_usd")
                        or order_resp.get("amountFilled")
                    )
                    avg_fill_price = _to_float(order_resp.get("avgFillPrice") or order_resp.get("avg_fill_price") or order_resp.get("averagePrice"))
                    fill = _normalized_entry_fill(
                        filled_usd=filled_usd,
                        filled_shares=filled_shares,
                        avg_fill_price=avg_fill_price,
                        fallback_price=pos.entry_price,
                    )

                    # FAK orders that are sequencer-delayed can return ambiguous
                    # matched payloads. Promote only when we can derive a coherent
                    # cost/share/price tuple.
                    if fill is None:
                        # Cancel the resting CLOB order so it doesn't hold collateral as a ghost bid
                        if pos.pending_entry_order_id:
                            await live_exit_executor.cancel_gtc_order(pos.pending_entry_order_id)
                        if live_exit_logger:
                            live_exit_logger.log_lifecycle(
                                position=pos, event="entry_zero_fill_cleanup",
                                raw_response_json=json.dumps(order_resp, default=str),
                            )
                        live_position_store.mark_closed(pos.position_id)
                        if live_executor:
                            live_executor.decrement_open_positions(match_id=pos.match_id)
                            live_executor.release_submitted_budget(pos.cost_usd or 0.0, match_id=pos.match_id)
                        print(f"LIVE ENTRY FAILED (ambiguous fill): {pos.market_name} {pos.side} order={pos.pending_entry_order_id} filled_usd={filled_usd} filled_shares={filled_shares}")
                        continue

                    filled_usd, filled_shares, avg_fill_price = fill
                    entry_order_id = pos.pending_entry_order_id
                    pos.state = "OPEN"
                    pos.cost_usd = filled_usd
                    pos.shares = filled_shares
                    pos.entry_price = avg_fill_price
                    pos.pending_entry_order_id = None

                    live_position_store.save()
                    print(f"LIVE ENTRY FILLED: {pos.market_name} {pos.side} order={entry_order_id} shares={filled_shares:.4f}")
                elif status in ("canceled", "killed", "rejected", "expired", "declined"):
                    if live_exit_logger:
                        live_exit_logger.log_lifecycle(
                            position=pos, event=f"entry_{status}",
                            raw_response_json=json.dumps(order_resp, default=str),
                        )
                    live_position_store.mark_closed(pos.position_id)
                    if live_executor:
                        live_executor.decrement_open_positions(match_id=pos.match_id)
                        live_executor.release_submitted_budget(pos.cost_usd or 0.0, match_id=pos.match_id)
                    print(f"LIVE ENTRY {status.upper()}: {pos.market_name} {pos.side} order={pos.pending_entry_order_id}")
                elif status in ("live", "open", ""):
                    # GTC order resting — cancel after LIVE_GTC_TIMEOUT_SEC.
                    # 2026-05-27 lowered 45→30: shadow analysis showed bulk of book
                    # reaction happens in first 10-30s; resting longer locks collateral
                    # without higher fill probability.
                    import os as _os
                    GTC_TIMEOUT = float(_os.getenv("LIVE_GTC_TIMEOUT_SEC", "30"))
                    age_sec = (time.time_ns() - (pos.entry_time_ns or 0)) / 1e9
                    if age_sec > GTC_TIMEOUT:
                        print(f"LIVE ENTRY GTC TIMEOUT ({age_sec:.0f}s): cancelling {pos.market_name} {pos.side} order={pos.pending_entry_order_id}")
                        await live_exit_executor.cancel_gtc_order(pos.pending_entry_order_id)
                        if live_exit_logger:
                            live_exit_logger.log_lifecycle(position=pos, event="entry_gtc_timeout")
                        live_position_store.mark_closed(pos.position_id)
                        if live_executor:
                            live_executor.decrement_open_positions(match_id=pos.match_id)
                            live_executor.release_submitted_budget(pos.cost_usd or 0.0, match_id=pos.match_id)

            # --- Poll pending GTC exit orders ---
            for pos in live_position_store.pending_gtc_positions():
                order_resp = await live_exit_executor.check_gtc_fill(pos.pending_exit_order_id)
                status = str(order_resp.get("status") or order_resp.get("orderStatus") or "").lower()
                filled_shares = _to_float(order_resp.get("filledShares") or order_resp.get("filled_shares") or order_resp.get("sizeMatched") or 0)

                if filled_shares and filled_shares >= pos.shares * 0.999:
                    live_position_store.mark_closed(pos.position_id)
                    if live_executor:
                        live_executor.decrement_open_positions(match_id=pos.match_id)
                        fill_price = _to_float(order_resp.get("avgFillPrice") or order_resp.get("avg_fill_price") or order_resp.get("averagePrice")) or pos.exit_order_price or 0.0
                        proceeds = filled_shares * fill_price
                        live_executor.add_realized_pnl(proceeds - (pos.cost_usd or 0.0))
                    print(f"LIVE EXIT GTC FILLED: {pos.market_name} {pos.side} order={pos.pending_exit_order_id}")
                    continue

                if status in ("matched", "filled"):
                    live_position_store.mark_closed(pos.position_id)
                    if live_executor:
                        live_executor.decrement_open_positions(match_id=pos.match_id)
                    print(f"LIVE EXIT GTC MATCHED: {pos.market_name} {pos.side} order={pos.pending_exit_order_id}")
                    continue

                # Cancel and repost if best bid has dropped >3 ticks below our posted price
                book = store.get(pos.token_id)
                bid = (_to_float(book.get("best_bid")) if book else None) or 0.0
                ask = (_to_float(book.get("best_ask")) if book else None) or 1.0
                exit_price = pos.exit_order_price or 0.0
                if bid > 0 and exit_price > 0 and bid < exit_price - 0.03:
                    print(f"LIVE EXIT GTC CANCEL+REPOST: {pos.market_name} bid={bid:.2f} was_posted={exit_price:.2f}")
                    await live_exit_executor.cancel_gtc_order(pos.pending_exit_order_id)
                    live_position_store.mark_open_again(pos.position_id)
                    # Repost below falls through to open_positions loop on next cycle

            # --- Trigger new exits for open positions ---
            for pos in live_position_store.open_positions():
                book = store.get(pos.token_id)
                if book and book.get("best_bid"):
                    bid_val = float(book["best_bid"])
                    if bid_val > pos.peak_bid:
                        pos.peak_bid = bid_val
                        # peak_bid is persisted when store is next saved

                pos_game = _last_steam_games.get(pos.match_id) if _last_steam_games else None
                decision = decide_live_exit(
                    position=pos,
                    book=book,
                    game_over_match_ids=game_over_match_ids,
                    adverse_token_ids=adverse_token_ids,
                    game=pos_game,
                )
                if not decision.should_exit:
                    continue

                if getattr(pos, "trader_kind", "event") == "value" and decision.reason == "game_over":
                    # VALUE is a settlement strategy. Do not sell at game_over;
                    # leave redemption/realized P&L to settlement accounting.
                    pos.exit_reason = "settlement_hold"
                    live_position_store.mark_closed(pos.position_id)
                    if live_executor:
                        live_executor.decrement_open_positions(match_id=pos.match_id)
                    if live_exit_logger:
                        live_exit_logger.log_lifecycle(position=pos, event="settlement_hold")
                    print(f"LIVE VALUE SETTLEMENT HOLD: {pos.market_name} {pos.side}")
                    continue

                print(f"LIVE EXIT TRIGGERED: {pos.market_name} {pos.side} reason={decision.reason}")
                live_position_store.mark_exiting(pos.position_id, decision.reason)

                # Find mapping for this position to get tick_size/neg_risk
                mapping = next((m for m in mappings if m.get("yes_token_id") == pos.token_id or m.get("no_token_id") == pos.token_id), {})

                attempt = await live_exit_executor.try_exit(
                    position=pos,
                    book=book,
                    reason=decision.reason,
                    mapping=mapping,
                )

                if live_exit_logger:
                    live_exit_logger.log_exit_attempt(attempt)

                # GTC order accepted → track as pending
                if attempt.order_id and attempt.order_status in ("live", "LIVE", "open", "delayed"):
                    live_position_store.mark_pending_exit_gtc(
                        pos.position_id,
                        attempt.order_id,
                        attempt.price_posted or 0.0,
                    )
                    print(f"LIVE EXIT GTC POSTED: {pos.market_name} {pos.side} price={attempt.price_posted} order={attempt.order_id}")
                elif attempt.shares_filled >= pos.shares * 0.999:
                    # Immediate fill (crossed as taker)
                    live_position_store.mark_closed(pos.position_id)
                    if live_executor:
                        live_executor.decrement_open_positions(match_id=pos.match_id)
                        resp = json.loads(attempt.raw_response_json) if attempt.raw_response_json else {}
                        fill_price = _to_float(resp.get("avgFillPrice") or resp.get("avg_fill_price") or resp.get("averagePrice")) or attempt.price_posted or pos.entry_price
                        proceeds = attempt.shares_filled * fill_price
                        live_executor.add_realized_pnl(proceeds - (pos.cost_usd or 0.0))
                    print(f"LIVE EXIT FILLED: {pos.market_name} {pos.side} status={attempt.order_status}")
                elif attempt.order_status == "rejected_balance":
                    # Mismatch between local state and exchange balance -> force close to stop the loop
                    live_position_store.mark_closed(pos.position_id)
                    if live_executor:
                        live_executor.decrement_open_positions(match_id=pos.match_id)
                    print(f"LIVE EXIT FORCE CLOSED (BALANCE ERR): {pos.market_name} {pos.side} reason={attempt.reason_if_rejected}")
                else:
                    live_position_store.mark_open_again(pos.position_id)
                    print(f"LIVE EXIT FAILED: {pos.market_name} {pos.side} status={attempt.order_status} reason={attempt.reason_if_rejected}")

    def _on_book_update():
        """Called after every Polymarket WS message. Checks TP/SL/horizon exits and book-move signals."""
        for cp in trader.check_exits(store, set(), None):
            position_logger.log_exit(cp)
            print(
                f"EXIT [{cp.exit_reason}] {cp.market_name} {cp.side} "
                f"entry={cp.entry_price:.4f} exit={cp.exit_price:.4f} "
                f"pnl=${cp.pnl_usd:+.2f} hold={cp.hold_sec:.0f}s"
            )

        # Book-move signal detection — runs on every WS tick, O(mappings) cost
        for m in mappings:
            yes_tok = m.get("yes_token_id", "")
            no_tok = m.get("no_token_id", "")
            for tok_id, tok_side, opposing_tok in [
                (yes_tok, "YES", no_tok),
                (no_tok, "NO", yes_tok),
            ]:
                if not tok_id or "TOKEN_ID_HERE" in str(tok_id):
                    continue
                book = store.get(tok_id)
                if not book:
                    continue
                sig = book_move_detector.on_book_update(
                    token_id=tok_id,
                    book=book,
                    steam_games=_last_steam_games,
                    mappings=mappings,
                )
                if not sig:
                    continue

                # --- Direction resolution ---
                # "up" on this token → buy this token
                # "down" on this token → buy the opposing token (it should be rising)
                if sig["direction"] == "up":
                    exec_tok_id   = tok_id
                    exec_tok_side = tok_side
                    exec_opp_tok  = opposing_tok
                    exec_book     = book
                else:
                    exec_tok_id   = opposing_tok
                    exec_tok_side = "NO" if tok_side == "YES" else "YES"
                    exec_opp_tok  = tok_id
                    exec_book     = store.get(opposing_tok) or {}

                exec_ask = exec_book.get("best_ask")
                exec_bid = exec_book.get("best_bid")
                exec_spread = (float(exec_ask) - float(exec_bid)) if exec_ask is not None and exec_bid is not None else None

                # --- Execution gates ---
                skip_reason = None
                if exec_ask is None:
                    skip_reason = "no_exec_ask"
                elif exec_spread is None:
                    skip_reason = "no_exec_spread"
                elif exec_spread > MAX_SPREAD:
                    skip_reason = f"spread_too_wide ({exec_spread:.3f})"
                elif sig["direction"] == "down":
                    # Buying opposing token — verify its book is fresh
                    opp_ns = exec_book.get("received_at_ns")
                    opp_age_ms = int((time.time_ns() - opp_ns) / 1_000_000) if opp_ns else 9999999
                    if opp_age_ms > MAX_BOOK_AGE_MS:
                        skip_reason = f"opposing_book_stale ({opp_age_ms}ms)"

                # --- Steam corroboration ---
                # Use a wider age window for directional confirmation only (3x trade freshness)
                _CORROBORATION_AGE_MS = MAX_STEAM_AGE_MS * 3
                steam_corroborated = None
                is_alpha_candidate = False
                
                if not skip_reason:
                    match_id = str(m.get("dota_match_id") or "")
                    game = _last_steam_games.get(match_id)
                    if game:
                        snap_ns = game.get("received_at_ns")
                        snap_age_ms = int((time.time_ns() - snap_ns) / 1_000_000) if snap_ns else 9999999
                        if snap_age_ms > _CORROBORATION_AGE_MS:
                            game = None  # too stale even for direction check
                    if game:
                        radiant_lead = game.get("radiant_lead")
                        yes_team = (m.get("yes_team") or "").strip().lower()
                        steam_radiant = (m.get("steam_radiant_team") or game.get("radiant_team") or "").strip().lower()
                        yes_is_radiant = bool(yes_team and steam_radiant and yes_team == steam_radiant)
                        if radiant_lead is not None:
                            steam_yes_direction = "up" if (yes_is_radiant and radiant_lead > 0) or \
                                                          (not yes_is_radiant and radiant_lead < 0) else "down"
                            expected_direction = "up" if exec_tok_side == "YES" else "down"
                            steam_corroborated = (steam_yes_direction == expected_direction)
                            if not steam_corroborated:
                                # Check if it qualifies as an Alpha candidate (strong move contradicting stale Steam)
                                if abs(sig["magnitude"]) >= BOOK_MOVE_ALPHA_THRESHOLD:
                                    is_alpha_candidate = True
                                else:
                                    skip_reason = "steam_contradicts"
                                    
                sig["steam_corroborated"] = steam_corroborated
                sig["traded"] = False
                sig["trade_skip_reason"] = skip_reason
                book_move_logger.log(sig)
                
                print(
                    f"BOOK_MOVE {m.get('name','?')[:35]} {exec_tok_side} "
                    f"dir={sig['direction']} mag={sig['magnitude']:+.4f} "
                    f"spread={exec_spread} steam_ok={steam_corroborated} "
                    f"{'-> EXECUTE' if not skip_reason and not is_alpha_candidate else ('-> QUEUE_ALPHA' if is_alpha_candidate else f'skip={skip_reason}')}"
                )

                if skip_reason:
                    continue

                if is_alpha_candidate:
                    # Queue it instead of executing immediately
                    _pending_book_moves.append({
                        "queued_at": time.time(),
                        "exec_tok_id": exec_tok_id,
                        "exec_tok_side": exec_tok_side,
                        "exec_opp_tok": exec_opp_tok,
                        "exec_ask": exec_ask,
                        "exec_bid": exec_bid,
                        "exec_spread": exec_spread,
                        "exec_book": exec_book,
                        "sig": sig,
                        "mapping": m,
                        "match_id": match_id
                    })
                    continue

                # Dedup: skip if we already attempted this exec token in the last 60s.
                # Prevents YES-up and NO-down signals from double-firing on the same market,
                # which causes order_version_mismatch errors from concurrent CLOB submissions.
                _now = time.time()
                if _now - _book_move_last_exec.get(exec_tok_id, 0) < _BOOK_MOVE_EXEC_COOLDOWN_SEC:
                    sig["trade_skip_reason"] = "exec_tok_cooldown"
                    book_move_logger.log(sig)
                    continue
                _book_move_last_exec[exec_tok_id] = _now

                match_id = str(m.get("dota_match_id") or "")
                # fair_price = current mid + full observed move (no +0.06 hard cap).
                # The wide cap allows price_cap to cover large moves that continue after signal fires.
                _exec_mid = (float(exec_ask) + float(exec_bid)) / 2 if exec_bid is not None else float(exec_ask)

                            # Scale magnitude for MATCH_WINNER (Series) markets
                expected_move = abs(sig["magnitude"])
                if m.get("market_type") == "MATCH_WINNER":
                    try:
                        p_next = float(m.get("p_next_yes") or 0.5)
                        p_next = max(0.01, min(0.99, p_next))
                        score_yes = int(m.get("series_score_yes") or 0)
                        score_no = int(m.get("series_score_no") or 0)
                        gnum = int(m.get("current_game_number") or m.get("game_number") or 1)
                        if gnum == 1:
                            sensitivity = 2 * p_next * (1 - p_next)
                        elif gnum == 2:
                            sensitivity = (1 - p_next) if score_yes >= score_no else p_next
                        else:
                            sensitivity = 1.0
                    except (TypeError, ValueError):
                        sensitivity = 0.5
                    expected_move *= sensitivity
                            
                _bm_fair = min(_exec_mid + expected_move, 0.95)
                trade_signal = {
                    "event_type": "BOOK_MOVE",
                    "event_schema_version": "cadence_v1",
                    "source_cadence_quality": "direct",
                    "token_id": exec_tok_id,
                    "fair_price": _bm_fair,
                    "executable_edge": _bm_fair - float(exec_ask),
                    "ask": float(exec_ask),
                    "bid": float(exec_bid) if exec_bid is not None else None,
                    "spread": exec_spread,
                    "ask_size": exec_book.get("ask_size"),
                    "expected_move": abs(sig["magnitude"]),
                    "lag": abs(sig["magnitude"]),
                    "target_size_usd": MAX_TRADE_USD,
                    "magnitude": sig["magnitude"],
                    "direction": sig["direction"],
                    "book_age_ms": sig.get("book_age_ms"),
                    "max_fill_price": 0.94,
                }

                if LIVE_TRADING and ENABLE_REAL_LIVE_TRADING and live_executor and live_logger:
                    if live_position_store and exec_opp_tok:
                        active_states = {"OPEN", "EXITING", "PENDING_ENTRY", "PENDING_EXIT_GTC"}
                        active_toks = {
                            p.token_id for p in live_position_store.positions.values()
                            if p.state in active_states
                        }
                        if str(exec_opp_tok) in active_toks:
                            print(f"BOOK_MOVE LIVE_SKIP opposing_position_exists: {m.get('name')} {exec_tok_side}")
                            sig["traded"] = False
                            continue
                    async def _exec_book_move(
                        _sig=trade_signal, _m=m, _match_id=match_id,
                        _tok=exec_tok_id, _side=exec_tok_side, _opp=exec_opp_tok,
                    ):
                        game_stub = {"match_id": _match_id, "game_time_sec": _last_steam_games.get(_match_id, {}).get("game_time_sec")}
                        attempt = await live_executor.try_buy(
                            signal=_sig, mapping=_m, game=game_stub, book_store=store,
                        )
                        asyncio.create_task(_log_live_attempt_with_markouts(attempt, store, live_logger))
                        print(
                            f"BOOK_MOVE LIVE {_m.get('name','?')[:30]} {_side} "
                            f"status={attempt.order_status} filled=${attempt.filled_size_usd:.2f} "
                            f"reason={attempt.reason_if_rejected}"
                        )
                    asyncio.create_task(_exec_book_move())
                    sig["traded"] = True
                else:
                    pos, reason = trader.enter(
                        signal=trade_signal,
                        token_id=exec_tok_id,
                        side=exec_tok_side,
                        book_store=store,
                        match_id=match_id,
                        market_name=m.get("name"),
                        opposing_token_id=exec_opp_tok,
                    )
                    if pos:
                        sig["traded"] = True
                        position_logger.log_entry(pos)
                        print(
                            f"BOOK_MOVE PAPER ENTER {m.get('name','?')[:30]} {exec_tok_side} "
                            f"price={pos.entry_price:.4f} cost=${pos.cost_usd:.2f}"
                        )
                        # Fix #4: track markouts on book-move entries
                        markout_row = {
                            "match_id": match_id,
                            "market_name": m.get("name"),
                            "event_type": "BOOK_MOVE",
                            "event_tier": "book",
                            "event_is_primary": True,
                            "event_direction": sig["direction"],
                            "token_id": exec_tok_id,
                            "side": exec_tok_side,
                            "decision": "paper_entry",
                            "skip_reason": None,
                            "reference_price": pos.entry_price,
                            "reference_bid": exec_bid,
                            "reference_ask": float(exec_ask),
                            "fair_price": trade_signal["fair_price"],
                            "hybrid_fair": None,
                            "executable_edge": trade_signal["fair_price"] - float(exec_ask),
                            "signal_timestamp_utc": sig.get("timestamp_utc"),
                        }
                        asyncio.create_task(
                            _log_signal_markouts(markout_row, exec_tok_id, store, signal_markout_logger)
                        )
                    else:
                        sig["trade_skip_reason"] = reason
                        print(f"BOOK_MOVE PAPER SKIP: {reason}")

    async def live_exit_loop():
        """Recurring exit check to avoid task backlog on heavy book traffic."""
        while True:
            try:
                await _check_live_exits()
            except Exception as e:
                print(f"Error in live_exit_loop: {e}")
            await asyncio.sleep(0.5)

    async def proactive_refresh_loop(session: aiohttp.ClientSession):
        """Proactively refreshes books for active match markets to prevent staleness rejections."""
        STALE_THRESHOLD_MS = 4000
        MAX_CONCURRENT = 8

        async def _refresh_one(tok_id: str) -> None:
            fresh = await fetch_fresh_book(session, tok_id, timeout_ms=1500)
            if fresh:
                b = store.update_direct(
                    tok_id,
                    best_bid=fresh.get("best_bid"),
                    best_ask=fresh.get("best_ask"),
                    bid_size=fresh.get("bid_size"),
                    ask_size=fresh.get("ask_size"),
                )
                if book_logger:
                    try:
                        _row = dict(b); _row["asset_id"] = tok_id
                        book_logger.log_book(_row, source_event_type="rest_refresh")
                    except Exception:
                        pass

        # Only REST-refresh a token if WS has ever delivered a book update for it.
        # Tokens with no WS data are closed/resolved markets that return 404 on REST.
        WS_SEEN_TIMEOUT_MS = 5 * 60 * 1000  # 5 minutes

        while True:
            try:
                # 2026-06-02 — Polymarket's WS now sends one snapshot on subscribe
                # then ~no incremental updates, so the in-memory book goes stale and
                # the bot rejects entries as book_stale. Gate REST refresh on
                # "match is LIVE per Steam" (NOT on WS recency) — otherwise the old
                # WS_SEEN_TIMEOUT logic stops refreshing live markets once the dead
                # WS stream has been silent >5 min. This makes REST the primary book
                # source for live matches; the cockpit proves the REST path is sound.
                live_ids = set(_last_steam_games.keys()) if _last_steam_games else set()
                # Grace window: a live game (esp. draft / gt=0) flickers in and out of
                # GetTopLive. Keep refreshing its books for REFRESH_GRACE_SEC after it
                # was last seen, so a brief feed drop doesn't blank the book and cost a
                # trade. Tracked in _match_last_live.
                _now = time.time()
                for _mid in live_ids:
                    _match_last_live[_mid] = _now
                eligible_ids = {mid for mid, ts in _match_last_live.items()
                                if (_now - ts) < REFRESH_GRACE_SEC}
                seen_tokens: set[str] = set()
                refresh_tokens: list[str] = []
                for m in mappings:
                    mid = str(m.get("dota_match_id") or "")
                    if not mid or mid in _PERSISTENT_GAME_OVER_MATCH_IDS:
                        continue
                    if mid not in eligible_ids:
                        continue  # live now, or live within the grace window
                    for tok_id in [m["yes_token_id"], m["no_token_id"]]:
                        if tok_id not in seen_tokens:
                            seen_tokens.add(tok_id)
                            refresh_tokens.append(tok_id)

                # Refresh all live-match tokens in parallel batches.
                for i in range(0, len(refresh_tokens), MAX_CONCURRENT):
                    batch = refresh_tokens[i:i + MAX_CONCURRENT]
                    await asyncio.gather(*[_refresh_one(t) for t in batch])

            except Exception as e:
                print(f"Error in proactive_refresh_loop: {e}")

            await asyncio.sleep(2.0)

    if run_live_infra:
        print(f"Starting GUARDED LIVE TEST with {len(mappings)} active mapping(s). (ENABLE_REAL_LIVE_TRADING={ENABLE_REAL_LIVE_TRADING})")
    else:
        print(f"Starting paper bot with {len(mappings)} active mapping(s). Checking for new games every {MAPPING_REFRESH_SECONDS}s.")

    # steam_loop now owns the heartbeat (writes it every iteration), so stop the
    # startup keep-alive — this restores real-hang detection for the main loop.
    _startup_hb_task.cancel()

    try:
        async with aiohttp.ClientSession() as session:
            if live_executor is not None:
                live_executor.set_session(session)
            tasks = [
                listen_books(asset_ids, store, book_logger=book_logger, on_book_update=_on_book_update,
                             live_game_count=lambda: len(_last_steam_games)),
                steam_loop(
                    store, trader, signal_logger, event_detector, signal_engine,
                    event_logger, position_logger, snapshot_logger, latency_logger,
                    live_executor, live_logger, rich_context_logger,
                    source_delay_logger, rescue_logger, match_winner_logger,
                    signal_markout_logger, mappings, asset_ids,
                    model_bundle=model_bundle, http_session=session,
                    live_position_store=live_position_store,
                    live_exit_executor=live_exit_executor,
                    live_exit_logger=live_exit_logger,
                    check_live_exits_fn=_check_live_exits,
                    shadow_logger=shadow_logger,
                    last_steam_games=_last_steam_games,
                    value_engine=value_engine,
                    value_logger=value_logger,
                    dswing_engine=dswing_engine,
                    dswing_logger=dswing_logger,
                ),
                proactive_refresh_loop(session),
            ]
            if run_live_infra:
                tasks.append(live_exit_loop())
            
            await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        # Gracefully stop background loggers to ensure queued rows are flushed to disk
        print(f"Flushing {len(loggers)} background loggers...")
        for logger in loggers:
            try:
                logger.stop()
            except Exception as e:
                print(f"Error stopping logger {getattr(logger, 'filename', 'unknown')}: {e}")

        summary = trader.summary()
        print(f"\nSession summary: {summary}")


def _setup_logging() -> None:
    """Configure root logging with rotating file + stdout stream handlers.

    After this is wired, drop any shell-level `>> logs/bot.log` redirect from
    the launch command — Python now writes to bot.log directly, with 100MB ×
    5-backup rotation. A shell redirect to the same path will interleave.
    """
    import logging
    from logging.handlers import RotatingFileHandler

    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        filename=os.path.join(log_dir, "bot.log"),
        maxBytes=100 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)


if __name__ == "__main__":
    _setup_logging()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Shutting down...")
