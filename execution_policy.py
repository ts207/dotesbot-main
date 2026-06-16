from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

POLICY_VERSION = "execution_policy_v1"


@dataclass(frozen=True)
class PolicyInput:
    mode: Literal["paper_research", "paper_live_parity", "dry_live", "real_live"]
    strategy_kind: str
    market_type: str
    token_id: str
    side: str
    signal: dict
    game: dict
    mapping: dict
    book: dict | None
    now_ns: int
    risk_state: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyResult:
    allowed: bool
    reason: str
    would_pass_live: bool
    live_skip_reason: str
    paper_only_bypass: bool
    price_cap: float | None
    size_usd: float | None
    risk_tags: tuple[str, ...]
    policy_version: str = POLICY_VERSION



def allow(
    *,
    reason: str = "allowed",
    price_cap: float | None = None,
    size_usd: float | None = None,
    risk_tags: tuple[str, ...] = (),
) -> PolicyResult:
    return PolicyResult(
        allowed=True,
        reason=reason,
        would_pass_live=True,
        live_skip_reason="",
        paper_only_bypass=False,
        price_cap=price_cap,
        size_usd=size_usd,
        risk_tags=risk_tags,
    )


def reject(
    reason: str,
    *,
    paper_only_bypass: bool = False,
    price_cap: float | None = None,
    size_usd: float | None = None,
    risk_tags: tuple[str, ...] = (),
) -> PolicyResult:
    return PolicyResult(
        allowed=False,
        reason=reason,
        would_pass_live=False,
        live_skip_reason=reason,
        paper_only_bypass=paper_only_bypass,
        price_cap=price_cap,
        size_usd=size_usd,
        risk_tags=risk_tags,
    )


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _age_ms(received_at_ns: Any, now_ns: int) -> int:
    ts = _int(received_at_ns)
    if not ts:
        return 10**12
    return max(0, int((now_ns - ts) / 1_000_000))


def _is_hold_to_settle(signal: Mapping[str, Any]) -> bool:
    horizon = signal.get("target_horizon")
    expected_hold = _int(signal.get("expected_hold_sec"))
    event_type = str(signal.get("event_type") or "")
    strategy_kind = str(signal.get("strategy_kind") or signal.get("event_family") or "")
    if horizon == "settlement" or expected_hold == 0:
        return True
    if event_type in {"VALUE", "VALUE_HOLD", "EVENT_TRIGGERED_VALUE"}:
        return True
    if strategy_kind in {"VALUE", "VALUE_EDGE", "EVENT_CONTINUATION_EDGE"}:
        return True
    return False


def _strategy_disabled(inp: PolicyInput) -> str | None:
    try:
        from config import RUNTIME_CONFIG
    except Exception:
        return None
    kind = inp.strategy_kind.upper()
    if kind in {"VALUE", "VALUE_EDGE"} and not RUNTIME_CONFIG.strategy.value_enabled:
        return "strategy_disabled:VALUE_EDGE"
    if kind in {"DSWING", "DSWING_EDGE"} and not RUNTIME_CONFIG.strategy.dswing_enabled:
        return "strategy_disabled:DSWING"
    if kind in {"EVENT_TRIGGERED_VALUE", "EVENT_CONTINUATION_EDGE", "EVENT_REVERSAL_EDGE"} and not RUNTIME_CONFIG.strategy.event_triggered_value_enabled:
        return "strategy_disabled:EVENT_TRIGGERED_VALUE"
    return None


def evaluate_policy(inp: PolicyInput) -> PolicyResult:
    """Common execution gates shared by live and live-parity paper paths.

    The first integration point is live_executor. Existing strategy-specific
    checks remain in place while callers migrate their duplicated gates here.
    """
    from config import (
        ALLOW_CONFIRMATION_ONLY_LIVE_TRADES,
        ALLOW_EVENT_TRADES,
        ALLOW_GAME_OVER_ONLY,
        DISABLE_STRUCTURE_TRADES,
        LIVE_ALLOWED_CADENCE_QUALITIES,
        LIVE_MIN_EVENT_QUALITY,
        LIVE_MIN_DECISIVE_STOMP_QUALITY,
        LIVE_REQUIRE_CADENCE_SCHEMA,
        DEFAULT_MAX_FILL_PRICE,
        MAX_BOOK_AGE_MS,
        MAX_DAILY_DRAWDOWN_USD,
        MAX_SOURCE_UPDATE_AGE_SEC,
        MAX_OPEN_POSITIONS,
        MAX_OPEN_USD_PER_MATCH,
        MAX_SPREAD,
        MAX_STEAM_AGE_MS,
        MAX_TOTAL_LIVE_USD,
        MIN_EXECUTABLE_EDGE,
        MIN_LAG,
        TRADE_EVENTS,
        VALUE_MAX_PER_MATCH,
    )
    from event_taxonomy import event_tier
    
    STRUCTURE_EVENTS = frozenset({
        "OBJECTIVE_CONVERSION_T2",
        "OBJECTIVE_CONVERSION_T3",
        "OBJECTIVE_CONVERSION_T4",
        "BASE_PRESSURE_T3_COLLAPSE",
        "BASE_PRESSURE_T4",
        "THRONE_EXPOSED",
    })

    strategy_disabled = _strategy_disabled(inp)
    if strategy_disabled:
        return reject(strategy_disabled, risk_tags=("strategy_disabled",))

    if not inp.mapping:
        return reject("mapping_valid:false", risk_tags=("mapping_valid",))
    if inp.mapping.get("mapping_state") == "quarantined":
        reason = str(inp.mapping.get("quarantine_reason") or "mapping_quarantined")
        return reject(f"mapping_quarantined:{reason}", risk_tags=("mapping_valid",))
    if str(inp.market_type or "").upper() not in {"MAP_WINNER", "MATCH_WINNER"}:
        return reject("unsupported_market_type", risk_tags=("unsupported_market_type",))

    if not ALLOW_EVENT_TRADES:
        return reject("event_trades_disabled", risk_tags=("event_trades_disabled",))
    if ALLOW_GAME_OVER_ONLY and not inp.game.get("game_over"):
        return reject("game_over_only", risk_tags=("game_over_only",))

    if inp.game.get("data_source") not in (None, "", "top_live"):
        return reject("non_top_live_source", risk_tags=("non_top_live_source",))
    source_age = _float(inp.game.get("source_update_age_sec"))
    if source_age is not None and source_age > MAX_SOURCE_UPDATE_AGE_SEC:
        return reject(
            f"source_update_stale:age_sec={source_age:.1f}_max={MAX_SOURCE_UPDATE_AGE_SEC:.1f}",
            risk_tags=("source_update_stale",),
        )
    steam_age = _age_ms(inp.game.get("received_at_ns"), inp.now_ns)
    if steam_age > MAX_STEAM_AGE_MS:
        return reject(
            f"steam_stale:age_ms={steam_age}_max={MAX_STEAM_AGE_MS}",
            risk_tags=("steam_stale",),
        )

    if inp.book is None:
        return reject("book_missing", risk_tags=("book_missing",))
    book_age = _age_ms(inp.book.get("received_at_ns"), inp.now_ns)
    if book_age > MAX_BOOK_AGE_MS:
        return reject(
            f"book_stale:age_ms={book_age}_max={MAX_BOOK_AGE_MS}",
            risk_tags=("book_stale",),
        )

    bid = _float(inp.book.get("best_bid"))
    ask = _float(inp.book.get("best_ask"))
    if bid is None or ask is None:
        return reject("missing_bid_or_ask", risk_tags=("missing_bid_or_ask",))
    if ask < 0.05:
        return reject(
            f"market_near_zero:ask={ask:.4f}",
            risk_tags=("market_near_zero",),
        )

    radiant_lead = _float(inp.game.get("radiant_lead"))
    if radiant_lead is not None:
        side_map = inp.mapping.get("steam_side_mapping")
        yes_book_ask = ask if str(inp.token_id) == str(inp.mapping.get("yes_token_id") or "") else None
        if yes_book_ask is not None:
            if side_map == "normal":
                yes_lead = radiant_lead
            elif side_map == "reversed":
                yes_lead = -radiant_lead
            else:
                yes_lead = None
            if yes_lead is not None and (
                (yes_lead > 5000 and yes_book_ask < 0.35)
                or (yes_lead < -5000 and yes_book_ask > 0.65)
            ):
                return reject(
                    f"orientation_flip_suspected:yes_lead={yes_lead:.0f}_yes_ask={yes_book_ask:.2f}",
                    risk_tags=("orientation_flip_suspected",),
                )

    spread = ask - bid
    if spread > MAX_SPREAD:
        return reject(
            f"spread_too_wide:spread={spread:.4f}_max={MAX_SPREAD:.4f}",
            risk_tags=("spread_too_wide",),
        )
    ask_size = _float(inp.book.get("ask_size") or inp.book.get("best_ask_size"))
    min_ask_size = _float(inp.signal.get("min_ask_size_usd"))
    if min_ask_size is not None and ask_size is not None and ask_size < min_ask_size:
        return reject(
            f"insufficient_ask_size:size={ask_size:.2f}_min={min_ask_size:.2f}",
            risk_tags=("insufficient_ask_size",),
        )

    max_fill = _float(inp.signal.get("max_fill_price"))
    if max_fill is None:
        max_fill = DEFAULT_MAX_FILL_PRICE
    if ask > max_fill:
        return reject(
            f"ask_above_max_fill:ask={ask:.4f}_cap={max_fill:.4f}",
            risk_tags=("ask_above_max_fill",),
        )
    if ask >= 0.95 and str(inp.signal.get("event_type") or "") != "THRONE_EXPOSED":
        return reject(
            f"terminal_price_chase:ask={ask:.4f}",
            risk_tags=("terminal_price_chase",),
        )

    event_type = str(inp.signal.get("event_type") or "")
    cluster_types = {e for e in str(inp.signal.get("cluster_event_types") or event_type).split("+") if e}
    is_book_move = event_type == "BOOK_MOVE"
    
    if event_type and TRADE_EVENTS and event_type not in {"VALUE", "VALUE_HOLD", "EVENT_TRIGGERED_VALUE", "DSWING"} and event_type not in TRADE_EVENTS:
        return reject("event_not_allowed", risk_tags=("event_not_allowed",))
        
    if not is_book_move:
        if DISABLE_STRUCTURE_TRADES and (event_type in STRUCTURE_EVENTS or cluster_types <= STRUCTURE_EVENTS):
            return reject("structure_trade_disabled", risk_tags=("structure_trade_disabled",))
        if TRADE_EVENTS and not (event_type in TRADE_EVENTS or cluster_types & TRADE_EVENTS):
            return reject("event_not_allowed", risk_tags=("event_not_allowed",))
        tier = event_tier(event_type)
        if event_type not in TRADE_EVENTS:
            if tier == "C" and not ALLOW_CONFIRMATION_ONLY_LIVE_TRADES:
                return reject("confirmation_only_event", risk_tags=("confirmation_only_event",))
            if tier in {"research", "block", "unknown"}:
                return reject(f"{tier}_event_not_live_tradable", risk_tags=("event_tier_not_live",))

    if event_type and event_type not in {"VALUE", "VALUE_HOLD", "EVENT_TRIGGERED_VALUE", "DSWING"}:
        if LIVE_REQUIRE_CADENCE_SCHEMA and inp.signal.get("event_schema_version") != "cadence_v1":
            return reject("cadence_schema_missing", risk_tags=("cadence_schema_missing",))
        cadence_quality = str(inp.signal.get("source_cadence_quality") or "")
        if LIVE_ALLOWED_CADENCE_QUALITIES and cadence_quality and cadence_quality not in LIVE_ALLOWED_CADENCE_QUALITIES:
            return reject(
                f"cadence_quality_bad:got={cadence_quality}",
                risk_tags=("cadence_quality_bad",),
            )
        event_quality = _float(inp.signal.get("event_quality"))
        if event_quality is not None and event_quality < LIVE_MIN_EVENT_QUALITY:
            return reject(
                f"event_quality_too_low:q={event_quality:.3f}_min={LIVE_MIN_EVENT_QUALITY:.3f}",
                risk_tags=("event_quality_too_low",),
            )
        if event_type == "POLL_DECISIVE_STOMP" and (event_quality is None or event_quality < LIVE_MIN_DECISIVE_STOMP_QUALITY):
            _q = f"{event_quality:.3f}" if event_quality is not None else "None"
            return reject(
                f"decisive_stomp_quality_too_low:q={_q}_min={LIVE_MIN_DECISIVE_STOMP_QUALITY:.3f}",
                risk_tags=("event_quality_too_low",),
            )

    if event_type == "POLL_DECISIVE_STOMP":
        if ask is not None and ask < 0.65:
            return reject(f"decisive_stomp_price_below_floor:ask={ask:.4f}_floor=0.6500", risk_tags=("decisive_stomp_price_below_floor",))
    if event_type == "POLL_FIGHT_SWING":
        if ask is not None and ask > 0.82:
            return reject(f"fight_swing_price_above_cap:ask={ask:.4f}_cap=0.8200", risk_tags=("fight_swing_price_above_cap",))
    if event_type == "OBJECTIVE_CONVERSION_T3":
        _edge_fresh = _float(inp.signal.get("executable_edge"))
        if ask is not None and ask > 0.85 and (_edge_fresh is None or _edge_fresh < 0.08):
            _e = f"{_edge_fresh:.4f}" if _edge_fresh is not None else "None"
            return reject(
                f"objective_conversion_t3_requires_8c_edge_above_85c:ask={ask:.4f}_edge={_e}",
                risk_tags=("objective_conversion_t3_edge",),
            )

    if not _is_hold_to_settle(inp.signal):
        edge = _float(inp.signal.get("executable_edge") or inp.signal.get("edge"))
        lag = _float(inp.signal.get("lag"))
        if edge is None or edge < MIN_EXECUTABLE_EDGE:
            val = f"{edge:.4f}" if edge is not None else "None"
            return reject(
                f"edge_too_small:edge={val}_min={MIN_EXECUTABLE_EDGE:.4f}",
                risk_tags=("edge_too_small",),
            )
        if lag is None or lag < MIN_LAG:
            val = f"{lag:.4f}" if lag is not None else "None"
            return reject(
                f"lag_too_small:lag={val}_min={MIN_LAG:.4f}",
                risk_tags=("lag_too_small",),
            )

    open_positions = _int(inp.risk_state.get("open_positions"))
    if open_positions is not None and open_positions >= MAX_OPEN_POSITIONS:
        return reject("max_open_positions", risk_tags=("max_open_positions",))
    submitted_usd = _float(inp.risk_state.get("total_submitted_usd")) or 0.0
    if submitted_usd >= MAX_TOTAL_LIVE_USD:
        return reject("max_total_live_usd", risk_tags=("max_total_live_usd",))
    daily_pnl = _float(inp.risk_state.get("daily_realized_pnl_usd")) or 0.0
    if daily_pnl <= -MAX_DAILY_DRAWDOWN_USD:
        return reject("daily_drawdown_breaker", risk_tags=("daily_drawdown_breaker",))
    match_used = _float(inp.risk_state.get("match_open_usd"))
    if match_used is not None and match_used >= MAX_OPEN_USD_PER_MATCH:
        return reject("max_open_usd_per_match", risk_tags=("max_open_usd_per_match",))

    # Match duplication logic (not applied to VALUE/DSWING which have separate cooldowns/logic)
    strategy_family = str(inp.signal.get("strategy_family") or "")
    if strategy_family not in {"VALUE", "DSWING"}:
        event_direction = str(inp.signal.get("event_direction") or "")
        existing = inp.risk_state.get("submitted_match_sides")
        existing_dirs = (set(existing) if isinstance(existing, (list, set))
                         else ({existing} if existing else set()))
        if existing_dirs:
            reason = ("match_already_submitted" if event_direction in existing_dirs
                      else "match_direction_conflict")
            return reject(reason, risk_tags=("match_direction_conflict",))

    # Strategy family cap logic
    strategy_family = str(inp.signal.get("strategy_family") or "")
    if strategy_family:
        family_cap = _float(inp.risk_state.get(f"{strategy_family}_max_live_usd")) or MAX_TOTAL_LIVE_USD
        family_used = _float(inp.risk_state.get("submitted_family_usd", {}).get(strategy_family)) or 0.0
        size_usd_req = _float(inp.signal.get("size_usd")) or 0.0  # approximate size
        if family_used + size_usd_req > family_cap:
            return reject(
                f"strategy_family_cap:{strategy_family}:used={family_used:.1f}_cap={family_cap:.1f}",
                risk_tags=("strategy_family_cap",),
            )
            
    # Value specific match cap
    if strategy_family == "VALUE":
        if match_used is not None and match_used + size_usd_req > VALUE_MAX_PER_MATCH:
            return reject(
                f"value_match_cap:used={match_used:.1f}_cap={VALUE_MAX_PER_MATCH:.1f}",
                risk_tags=("value_match_cap",),
            )

    return allow(risk_tags=("hold_to_settle_edge_lag_bypass",) if _is_hold_to_settle(inp.signal) else ())


def result_for_existing_decision(allowed: bool, reason: str, **kwargs: Any) -> PolicyResult:
    if allowed:
        return allow(reason=reason or "allowed", **kwargs)
    return reject(reason or "rejected", **kwargs)


def signal_policy_fields(result: PolicyResult) -> dict[str, Any]:
    return {
        "would_pass_live_gates": result.would_pass_live,
        "would_pass_live": result.would_pass_live,
        "live_skip_reason": result.live_skip_reason,
        "paper_only_bypass": result.paper_only_bypass or not result.would_pass_live,
        "policy_allowed": result.allowed,
        "policy_reason": result.reason,
        "policy_version": result.policy_version,
        "risk_tags": ",".join(result.risk_tags),
    }


def now_ns() -> int:
    return time.time_ns()
