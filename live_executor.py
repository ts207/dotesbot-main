from __future__ import annotations

import asyncio
import datetime
import json
import logging
import math
import os
import time
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

from config import (
    ALLOW_EVENT_TRADES,
    ALLOW_CONFIRMATION_ONLY_LIVE_TRADES,
    ALLOW_GAME_OVER_ONLY,
    DEFAULT_MAX_FILL_PRICE,
    DISABLE_STRUCTURE_TRADES,
    ENABLE_REAL_LIVE_TRADING,
    LIVE_FAK_BUFFER_TICKS,
    LIVE_GTC_ENTER_AT_MID,
    LIVE_ORDER_TYPE,
    LIVE_ALLOWED_CADENCE_QUALITIES,
    LIVE_MIN_EVENT_QUALITY,
    LIVE_MIN_DECISIVE_STOMP_QUALITY,
    LIVE_REQUIRE_CADENCE_SCHEMA,
    LIVE_SAFETY_MARGIN,
    LIVE_TICK_SIZE,
    MAKER_EXIT_MODE,
    MAX_BOOK_AGE_MS,
    MAX_OPEN_POSITIONS,
    MAX_SPREAD,
    MAX_STEAM_AGE_MS,
    MAX_TOTAL_LIVE_USD,
    MAX_DAILY_DRAWDOWN_USD,
    MAX_TRADE_USD,
    MAX_OPEN_USD_PER_MATCH,
    MIN_EXECUTABLE_EDGE,
    MIN_LAG,
    TRADE_EVENTS,
    VALUE_FAK_BUFFER_TICKS,
    VALUE_MAX_PER_MATCH,
    VALUE_REENTRY_COOLDOWN_SEC,
)
import aiohttp
from event_taxonomy import PREMIUM_EVENT_FILTERS, event_tier
from execution_policy import (
    POLICY_VERSION,
    PolicyInput,
    PolicyResult,
    evaluate_policy,
    result_for_existing_decision,
)
from signal_engine import age_ms
from mapping_validator import validate_mapping_identity
from live_state import load_live_state, save_live_state
from disk_guard import DiskGuard

STRUCTURE_EVENTS = frozenset({
    "OBJECTIVE_CONVERSION_T2",
    "OBJECTIVE_CONVERSION_T3",
    "OBJECTIVE_CONVERSION_T4",
    "BASE_PRESSURE_T3_COLLAPSE",
    "BASE_PRESSURE_T4",
    "THRONE_EXPOSED",
})

_ALLOWED_ORDER_TYPES = {"FAK", "FOK", "GTC"}

_USDC_BALANCE_PATH = os.path.join("logs", "usdc_balance.json")

# B2: edge-weighted sizing cap. order_usd is multiplied by min(EDGE_SIZE_MAX_MULT,
# edge / MIN_EXECUTABLE_EDGE), floored at 1.0. Set to 1.0 to disable scaling.
EDGE_SIZE_MAX_MULT: float = 1.0  # disabled 2026-05-26 to keep trade size fixed at MAX_TRADE_USD

# Premium-event sizing multiplier — added 2026-05-26 after signal-quality audit
# identified specific (event × feature) combos with ~3x baseline EV.
# When the trigger matches PREMIUM_EVENT_FILTERS, multiply order_usd by this.
PREMIUM_SIZE_MULT: float = 2.0


def _persist_usdc_balance_snapshot(balance: float, at_ns: int) -> None:
    """Write the latest USDC balance reading so the dashboard can surface it.

    The in-memory cache on LiveExecutor lives only inside the bot process;
    dashboard runs separately, so we mirror the value to a tiny JSON file.
    """
    try:
        from atomic_writes import atomic_json_write
        atomic_json_write(_USDC_BALANCE_PATH, {"usdc_balance": float(balance), "checked_at_ns": int(at_ns)})
    except OSError as exc:
        logger.warning("[balance_gate] persist failed: %s", exc)


def round_down_to_tick(price: float, tick_size: str | float = LIVE_TICK_SIZE) -> float:
    """Round a probability price down to Polymarket's tick grid."""
    try:
        p = Decimal(str(price))
        tick = Decimal(str(tick_size))
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise ValueError(f"invalid price/tick: price={price!r} tick_size={tick_size!r}") from exc
    if tick <= 0:
        raise ValueError("tick_size must be positive")
    return float((p / tick).to_integral_value(rounding=ROUND_DOWN) * tick)


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    if hasattr(value, "dict"):
        return _jsonable(value.dict())
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return str(value)


def _value_signal_strategy_meta(signal: Any) -> tuple[str, str, str | None, bool, bool]:
    signal_kind = signal.__class__.__name__
    if signal_kind == "EventTriggeredValueSignal":
        return (
            "EVENT_REVERSAL_EDGE" if signal.is_reversal else "EVENT_CONTINUATION_EDGE",
            "EVENT",
            signal.actual_event_type,
            signal.is_reversal,
            signal.is_continuation,
        )
    if signal_kind == "DSwingSignal":
        return ("DSWING", "DSWING", None, False, False)
    return ("VALUE_EDGE", "VALUE", None, False, False)


def _strategy_family_cap_usd(strategy_family: str) -> float:
    env_key = f"{strategy_family.upper()}_MAX_LIVE_USD"
    try:
        return float(os.getenv(env_key, str(MAX_TOTAL_LIVE_USD)))
    except (TypeError, ValueError):
        return MAX_TOTAL_LIVE_USD


def _response_to_dict(resp: Any) -> dict[str, Any]:
    data = _jsonable(resp)
    if isinstance(data, dict):
        return data
    return {"raw": data}


def _status_from_response(resp: dict[str, Any]) -> str:
    # Polymarket V2 status can be in several places
    status = resp.get("status") or resp.get("orderStatus") or resp.get("state")
    if status:
        return str(status)
    if resp.get("success") is True:
        # Check for matching keys that imply it's NOT delayed
        fill_keys = ("filledShares", "filled_shares", "filledSizeUsd", "filled_size_usd", "amountFilled", "matchedAmount", "matched_amount")
        if any(resp.get(k) for k in fill_keys):
            return "success"
        # If success=True but no matching yet, it might be accepted by sequencer but not yet processed
        return "accepted"
    if resp.get("success") is False:
        return "rejected"
    return "unknown"


def _error_from_response(resp: dict[str, Any]) -> str:
    return str(resp.get("errorMsg") or resp.get("error") or resp.get("message") or "")


def _filled_usd_from_response(resp: dict[str, Any], requested_usd: float) -> float:
    """Best-effort filled-spend extraction for a BUY market order response.

    Polymarket response schemas can differ by client version. Prefer explicit USD
    filled/spent fields. If the response is a successful matched response without
    explicit fields, conservatively assume the requested amount filled so budget
    accounting errs on the safe side.
    """
    status_str = _status_from_response(resp).lower()
    
    # "delayed" and "live" mean the order is in the sequencer/book but not yet filled.
    # "accepted" means it was accepted but fill info is not in this response.
    if status_str in {"delayed", "live", "accepted"}:
        return 0.0

    explicit_keys = (
        "filledSizeUsd", "filled_size_usd", "filledAmountUsd", "filled_amount_usd",
        "amountFilled", "filledAmount", "filled", "filled_size",
    )
    for key in explicit_keys:
        value = _to_float(resp.get(key))
        if value is not None and value >= 0:
            return min(value, requested_usd)

    taking = _to_float(resp.get("takingAmount") or resp.get("taking_amount"))
    making = _to_float(resp.get("makingAmount") or resp.get("making_amount"))
    # For a BUY market order, clients may report either USDC spent or shares
    # received depending on perspective. Avoid treating a large share count as USD.
    if taking is not None and 0 <= taking <= requested_usd * 1.05:
        return min(taking, requested_usd)
    if making is not None and 0 <= making <= requested_usd * 1.05:
        return min(making, requested_usd)

    if resp.get("success") is True and status_str in {"matched", "success"}:
        return requested_usd
    if status_str in {"matched"}:
        return requested_usd
    return 0.0


def _avg_fill_price(resp: dict[str, Any], default_price: float, filled_usd: float) -> float | None:
    for key in ("avgFillPrice", "avg_fill_price", "averagePrice", "price"):
        value = _to_float(resp.get(key))
        if value is not None and value > 0:
            return value
    shares = _to_float(resp.get("shares") or resp.get("filledShares") or resp.get("filled_shares"))
    if shares and filled_usd > 0:
        return filled_usd / shares
    return default_price if filled_usd > 0 else None


def _filled_shares_from_sell_response(resp: dict[str, Any], requested_shares: float) -> float:
    """Parse explicit share fills only.

    Do not infer SELL closure from ambiguous fields like amountFilled,
    filledAmount, takingAmount, or makingAmount because those may be USDC.
    """
    status_str = _status_from_response(resp).lower()
    if status_str in {"delayed", "live", "accepted"}:
        return 0.0

    explicit_share_keys = (
        "filledShares",
        "filled_shares",
        "shares",
        "matchedShares",
        "matched_shares",
        "sizeMatched",
        "size_matched",
    )
    for key in explicit_share_keys:
        value = _to_float(resp.get(key))
        if value is not None and value >= 0:
            return min(value, requested_shares)
    return 0.0


@dataclass
class LiveOrderAttempt:
    event_type: str
    event_direction: str
    token_id: str
    side: str
    fair_price: float | None
    best_ask: float | None
    price_cap: float | None
    edge: float | None
    lag: float | None
    spread: float | None
    book_age_ms: int | None
    steam_age_ms: int | None
    order_type: str
    submitted_size_usd: float
    event_quality: float | None = None
    event_schema_version: str | None = None
    source_cadence_quality: str | None = None
    filled_size_usd: float = 0.0
    avg_fill_price: float | None = None
    order_status: str = "not_submitted"
    order_id: str | None = None
    reason_if_rejected: str = ""
    market_name: str | None = None
    match_id: str | None = None
    game_time_sec: int | None = None
    raw_response_json: str = ""
    created_at_ns: int = 0
    submit_start_ns: int | None = None
    response_received_ns: int | None = None
    submit_latency_ms: float | None = None
    # "event" remains for legacy diagnostic attempts; active entries use
    # "value" or "dswing".
    trader_kind: str = "event"
    # Active strategies leave this None; DSWING exits on map-end convergence.
    exit_horizon_sec: int | None = None
    signal_id: str | None = None
    strategy_kind: str | None = None
    strategy_family: str | None = None
    strategy_subtype: str | None = None
    is_reversal: bool | None = None
    is_continuation: bool | None = None
    policy_allowed: bool | None = None
    policy_reason: str = ""
    would_pass_live: bool | None = None
    live_skip_reason: str = ""
    paper_only_bypass: bool = False
    policy_version: str = POLICY_VERSION
    risk_tags: tuple[str, ...] = ()
    manual_operator: str | None = None
    manual_source: str | None = None
    manual_pre_trade_book: str | None = None
    model_version: str | None = None
    token_net_worth_lead: float | None = None
    token_score_margin: float | None = None
    radiant_net_worth: float | None = None
    dire_net_worth: float | None = None
    radiant_score: float | None = None
    dire_score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["risk_tags"] = ",".join(self.risk_tags)
        return data


class LiveCLOBClient:
    """Thin wrapper around py-clob-client (0.34.x).

    Imported lazily so paper mode does not require live trading credentials.
    """

    def __init__(self):
        try:
            from py_clob_client_v2.client import ClobClient
            from py_clob_client_v2.clob_types import (
                ApiCreds,
                MarketOrderArgs,
                OrderArgsV2,
                OrderPayload,
                OrderType,
                PartialCreateOrderOptions,
            )
        except Exception as exc:
            raise RuntimeError(
                "Live trading requires py-clob-client-v2. "
                "Install with: pip install py-clob-client-v2 --break-system-packages"
            ) from exc

        host = os.getenv("POLY_CLOB_HOST", "https://clob.polymarket.com")
        chain_id = int(os.getenv("POLY_CHAIN_ID", "137"))
        private_key = os.getenv("POLY_PRIVATE_KEY") or os.getenv("PK")
        if not private_key:
            raise RuntimeError("Missing POLY_PRIVATE_KEY/PK for live trading")

        creds = ApiCreds(
            api_key=os.getenv("POLY_CLOB_API_KEY") or os.getenv("CLOB_API_KEY"),
            api_secret=os.getenv("POLY_CLOB_SECRET") or os.getenv("CLOB_SECRET"),
            api_passphrase=os.getenv("POLY_CLOB_PASS_PHRASE") or os.getenv("CLOB_PASS_PHRASE"),
        )
        kwargs: dict[str, Any] = {
            "host": host,
            "chain_id": chain_id,
            "key": private_key,
            "creds": creds,
        }
        signature_type = os.getenv("POLY_SIGNATURE_TYPE")
        if signature_type:
            kwargs["signature_type"] = int(signature_type)
        funder = os.getenv("POLY_FUNDER_ADDRESS") or os.getenv("FUNDER_ADDRESS")
        if funder:
            kwargs["funder"] = funder

        self._client = ClobClient(**kwargs)
        self._lock = asyncio.Lock()
        self._MarketOrderArgs = MarketOrderArgs
        self._OrderArgsV2 = OrderArgsV2
        self._OrderPayload = OrderPayload
        self._OrderType = OrderType
        self._Options = PartialCreateOrderOptions

    async def buy_fak_market(self, *, token_id: str, amount_usd: float, price_cap: float, tick_size: str, neg_risk: bool) -> dict[str, Any]:
        order_args = self._MarketOrderArgs(
            token_id=str(token_id),
            amount=float(amount_usd),
            side="BUY",
            price=float(price_cap),
        )
        options = self._Options(tick_size=str(tick_size), neg_risk=bool(neg_risk) or None)
        async with self._lock:
            resp = await asyncio.to_thread(
                self._client.create_and_post_market_order,
                order_args,
                options,
                self._OrderType.FAK,
            )
        return _response_to_dict(resp)

    async def buy_fok_market(self, *, token_id: str, amount_usd: float, price_cap: float, tick_size: str, neg_risk: bool) -> dict[str, Any]:
        order_args = self._MarketOrderArgs(
            token_id=str(token_id),
            amount=float(amount_usd),
            side="BUY",
            price=float(price_cap),
        )
        options = self._Options(tick_size=str(tick_size), neg_risk=bool(neg_risk) or None)
        async with self._lock:
            resp = await asyncio.to_thread(
                self._client.create_and_post_market_order,
                order_args,
                options,
                self._OrderType.FOK,
            )
        return _response_to_dict(resp)

    async def buy_gtc_limit(self, *, token_id: str, amount_usd: float, price_cap: float, tick_size: str, neg_risk: bool, limit_price: float | None = None) -> dict[str, Any]:
        """GTC limit buy — posts at limit_price (or price_cap if not given) and rests until filled or cancelled.
        size is in shares = amount_usd / order_price."""
        order_price = limit_price if limit_price is not None else price_cap
        shares = round(amount_usd / order_price, 4)
        order_args = self._OrderArgsV2(
            token_id=str(token_id),
            price=float(order_price),
            size=float(shares),
            side="BUY",
        )
        options = self._Options(tick_size=str(tick_size), neg_risk=bool(neg_risk) or None)
        async with self._lock:
            resp = await asyncio.to_thread(
                self._client.create_and_post_order,
                order_args,
                options,
                self._OrderType.GTC,
            )
        return _response_to_dict(resp)

    async def sell_gtc_limit(
        self,
        *,
        token_id: str,
        shares: float,
        price_floor: float,
        tick_size: str,
        neg_risk: bool,
    ) -> dict[str, Any]:
        """GTC limit sell at bid price — fills immediately if bid exists, rests otherwise.
        GTC sells do not require USDC balance (only the conditional tokens being sold)."""
        order_args = self._OrderArgsV2(
            token_id=str(token_id),
            price=float(price_floor),
            size=float(shares),
            side="SELL",
        )
        options = self._Options(tick_size=str(tick_size), neg_risk=bool(neg_risk) or None)
        async with self._lock:
            resp = await asyncio.to_thread(
                self._client.create_and_post_order,
                order_args,
                options,
                self._OrderType.GTC,
            )
        return _response_to_dict(resp)

    async def get_order_status(self, order_id: str) -> dict[str, Any]:
        resp = await asyncio.to_thread(self._client.get_order, order_id)
        return _response_to_dict(resp)

    async def cancel_order_by_id(self, order_id: str) -> dict[str, Any]:
        async with self._lock:
            resp = await asyncio.to_thread(
                self._client.cancel_order,
                self._OrderPayload(orderID=order_id),
            )
        return _response_to_dict(resp)

    async def cancel_all_orders(self) -> dict[str, Any]:
        async with self._lock:
            resp = await asyncio.to_thread(self._client.cancel_all)
        return _response_to_dict(resp)

    async def get_usdc_balance(self) -> float | None:
        """CLOB-side USDC collateral balance, in USD (6-decimal -> float).

        Returns None on parse failure. Raises on network/auth failure so the
        caller can decide whether to fall back to cached state.
        """
        from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        async with self._lock:
            resp = await asyncio.to_thread(self._client.get_balance_allowance, params)
        raw = resp.get("balance") if isinstance(resp, dict) else getattr(resp, "balance", None)
        if raw is None:
            return None
        try:
            return float(raw) / 1_000_000.0
        except (TypeError, ValueError):
            return None

    async def get_conditional_balance(self, token_id: str) -> float | None:
        """CLOB-side conditional-token balance, in shares (6-decimal -> float)."""
        from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams
        params = BalanceAllowanceParams(
            asset_type=AssetType.CONDITIONAL,
            token_id=str(token_id),
        )
        async with self._lock:
            resp = await asyncio.to_thread(self._client.get_balance_allowance, params)
        raw = resp.get("balance") if isinstance(resp, dict) else getattr(resp, "balance", None)
        if raw is None:
            return None
        try:
            return float(raw) / 1_000_000.0
        except (TypeError, ValueError):
            return None

    async def get_order_book(self, token_id: str) -> dict[str, Any]:
        async with self._lock:
            resp = await asyncio.to_thread(self._client.get_order_book, str(token_id))
        return _response_to_dict(resp)


@dataclass
class LiveExitAttempt:
    position_id: str
    token_id: str
    match_id: str
    reason: str
    shares_requested: float
    shares_filled: float
    best_bid: float | None
    best_ask: float | None
    price_posted: float | None  # GTC limit price posted
    order_status: str
    order_id: str | None = None  # GTC order_id for polling
    reason_if_rejected: str = ""
    raw_response_json: str = ""
    submit_start_ns: int | None = None
    response_received_ns: int | None = None
    submit_latency_ms: float | None = None

    def to_dict(self):
        return asdict(self)


def _order_id_from_response(resp: dict[str, Any]) -> str | None:
    for key in ("orderID", "order_id", "orderId", "id"):
        val = resp.get(key)
        if val:
            return str(val)
    return None


class LiveExitExecutor:
    def __init__(self, client: Any | None = None):
        self.client = client

    async def try_exit(self, *, position: Any, book: dict | None, reason: str, mapping: dict) -> LiveExitAttempt:
        """GTC limit sell at current bid — fills immediately if bid exists, rests if not.
        GTC sells do not require USDC balance; FAK sells incorrectly do."""
        bid = book.get("best_bid") if book else None
        ask = book.get("best_ask") if book else None

        if bid is None and reason not in {"map_end_convergence", "game_over"}:
            return LiveExitAttempt(
                position_id=position.position_id,
                token_id=position.token_id,
                match_id=position.match_id,
                reason=reason,
                shares_requested=position.shares,
                shares_filled=0.0,
                best_bid=None,
                best_ask=None,
                price_posted=None,
                order_status="rejected_precheck",
                reason_if_rejected="missing_bid",
            )

        tick_size = str(mapping.get("tick_size") or LIVE_TICK_SIZE)
        neg_risk = bool(mapping.get("neg_risk", False))

        tick = float(tick_size)
        max_price = round_down_to_tick(1.0 - tick, tick_size)

        # Use entry price minus safety margin as floor — CLOB bids can show 0.001
        # even when AMM fair value is 0.40+. Never sell below this floor.
        entry_price = float(getattr(position, "entry_price", 0) or 0)
        safety_margin = float(LIVE_SAFETY_MARGIN)
        min_price = max(round_down_to_tick(entry_price - safety_margin, tick_size), tick)
        # Use the current book bid if it's meaningfully above the floor; otherwise hold at floor.
        # Keep the raw bid separate: forced exits should hit/join the bid, not improve above it.
        if bid is not None:
            raw_bid_price = min(round_down_to_tick(float(bid), tick_size), max_price)
            bid_price = raw_bid_price
            
            maker_price = None
            if MAKER_EXIT_MODE:
                # Aggressive maker: post at best_bid + 1 tick to be top of book
                maker_price = min(round_down_to_tick(bid_price + tick, tick_size), max_price)
        else:
            raw_bid_price = min_price
            bid_price = min_price
            maker_price = min_price
            # But don't exceed current ask (if we do, we might as well just hit the bid or ask)
            # Actually, if we post at best_ask, we are joining the queue.
            # If we post at best_bid + tick, we are the new best ask.
        if ask is not None:
            ask_price = min(round_down_to_tick(float(ask), tick_size), max_price)
            bid_price = min(maker_price, ask_price) if maker_price is not None else ask_price
        else:
            bid_price = maker_price if maker_price is not None else min_price

        # For forced exits (time/loss/game-over), always post at the current bid.
        # Flooring to entry - safety_margin posts above the book and leaves the order resting.
        _forced_exit_reasons = {
            "horizon", "stop_loss", "latency_edge_timeout",
            "game_over", "max_hold_timeout", "adverse_event",
            "map_end_convergence",
        }
        if reason in _forced_exit_reasons:
            price_floor = raw_bid_price
        else:
            price_floor = bid_price if bid_price >= min_price else min_price
        price_floor = min(price_floor, max_price)

        attempt = LiveExitAttempt(
            position_id=position.position_id,
            token_id=position.token_id,
            match_id=position.match_id,
            reason=reason,
            shares_requested=position.shares,
            shares_filled=0.0,
            best_bid=float(bid) if bid is not None else None,
            best_ask=float(ask) if ask is not None else None,
            price_posted=price_floor,
            order_status="not_submitted",
        )

        attempt.submit_start_ns = time.time_ns()
        if not ENABLE_REAL_LIVE_TRADING:
            attempt.response_received_ns = time.time_ns()
            attempt.order_status = "filled"
            attempt.reason_if_rejected = "paper_simulated"
            attempt.order_id = f"paper_exit_{time.time_ns()}"
            attempt.shares_filled = position.shares
            return attempt

        if self.client is None:
            self.client = LiveCLOBClient()

        try:
            resp = await self.client.sell_gtc_limit(
                token_id=position.token_id,
                shares=position.shares,
                price_floor=price_floor,
                tick_size=tick_size,
                neg_risk=neg_risk,
            )
            attempt.response_received_ns = time.time_ns()
        except Exception as exc:
            attempt.response_received_ns = time.time_ns()
            attempt.order_status = "exception"
            error_msg = repr(exc)
            attempt.reason_if_rejected = error_msg
            if "not enough balance" in error_msg.lower() or "insufficient balance" in error_msg.lower():
                attempt.order_status = "rejected_balance"
            if attempt.submit_start_ns and attempt.response_received_ns:
                attempt.submit_latency_ms = round((attempt.response_received_ns - attempt.submit_start_ns) / 1_000_000, 2)
            return attempt

        if attempt.submit_start_ns and attempt.response_received_ns:
            attempt.submit_latency_ms = round(
                (attempt.response_received_ns - attempt.submit_start_ns) / 1_000_000,
                2,
            )
        attempt.raw_response_json = json.dumps(_jsonable(resp), sort_keys=True)[:4000]
        attempt.order_status = _status_from_response(resp)
        attempt.reason_if_rejected = _error_from_response(resp)
        if "not enough balance" in attempt.reason_if_rejected.lower() or "insufficient balance" in attempt.reason_if_rejected.lower():
            attempt.order_status = "rejected_balance"
        attempt.order_id = _order_id_from_response(resp)
        attempt.shares_filled = _filled_shares_from_sell_response(resp, position.shares)

        return attempt

    async def check_gtc_fill(self, order_id: str) -> dict[str, Any]:
        """Poll the CLOB for a GTC order's current fill status."""
        if self.client is None:
            self.client = LiveCLOBClient()
        try:
            resp = await self.client.get_order_status(order_id)
            return _response_to_dict(resp) if isinstance(resp, dict) else {"raw": resp}
        except Exception as exc:
            return {"error": repr(exc)}

    async def cancel_gtc_order(self, order_id: str) -> dict[str, Any]:
        """Cancel a resting GTC exit order."""
        if self.client is None:
            self.client = LiveCLOBClient()
        try:
            resp = await self.client.cancel_order_by_id(order_id)
            return _response_to_dict(resp) if isinstance(resp, dict) else {"raw": resp}
        except Exception as exc:
            return {"error": repr(exc)}

    async def get_conditional_balance(self, token_id: str) -> float | None:
        if self.client is None:
            self.client = LiveCLOBClient()
        return await self.client.get_conditional_balance(token_id)

    async def cancel_all_open_orders(self) -> None:
        """Cancel all resting CLOB orders on startup to clear stale GTC bids/asks."""
        if self.client is None:
            self.client = LiveCLOBClient()
        try:
            result = await self.client.cancel_all_orders()
            logger.info("Startup cancel_all_orders: %s", result)
        except Exception as exc:
            logger.warning("Startup cancel_all_orders failed: %s", exc)


class LiveExecutor:
    """Guarded $10 live-test executor.

    It only sends capped BUY market orders with FAK/FOK semantics and keeps hard
    in-process budget counters. Persistence is intentionally simple: this is for
    a tiny path test, not unattended trading.
    """

    def __init__(self, client: Any | None = None):
        self.client = client
        self._http_session: aiohttp.ClientSession | None = None
        self.disk_guard = DiskGuard()
        state = load_live_state(mode=self._policy_mode())
        self.total_submitted_usd = float(state.get("total_submitted_usd", 0.0))
        self.total_filled_usd = float(state.get("total_filled_usd", 0.0))
        self.open_positions = int(state.get("open_positions", 0))
        self.daily_realized_pnl_usd = float(state.get("daily_realized_pnl_usd", 0.0))
        self.last_reset_date = str(state.get("last_reset_date", ""))
        
        today_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        if self.last_reset_date != today_date:
            self.daily_realized_pnl_usd = 0.0
            self.last_reset_date = today_date
            self._submitted_match_sides = state.get("submitted_match_sides", {})
            self._submitted_match_usd = state.get("submitted_match_usd", {})
            self._submitted_family_usd = state.get("submitted_family_usd", {})
            self._save()
        else:
            self._submitted_match_sides = state.get("submitted_match_sides", {})
            self._submitted_match_usd = state.get("submitted_match_usd", {})
            self._submitted_family_usd = state.get("submitted_family_usd", {})

        # In-memory only — per-direction re-entry cooldown for value-family entries.
        # Key: "value|<match_id>|<direction>". Value: last_entry_time_ns.
        self._value_last_entry_ns: dict[str, int] = {}

        # USDC balance gate cache. Refreshed every BALANCE_CACHE_TTL_SEC; stale
        # values up to BALANCE_CACHE_STALE_MAX_SEC are accepted if the fresh
        # fetch fails. None = never successfully fetched.
        self._balance_cache_usd: float | None = None
        self._balance_cache_at_ns: int = 0
        self._delayed_resolution_callback: Any | None = None

    BALANCE_CACHE_TTL_SEC: float = 5.0
    BALANCE_CACHE_STALE_MAX_SEC: float = 60.0

    def set_delayed_resolution_callback(self, callback: Any) -> None:
        """Register a callback that logs delayed-order terminal states."""
        self._delayed_resolution_callback = callback

    async def _emit_delayed_resolution(self, attempt: LiveOrderAttempt | None) -> None:
        if attempt is None or self._delayed_resolution_callback is None:
            return
        try:
            result = self._delayed_resolution_callback(attempt)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.warning("[delayed_poll] resolution callback failed: %s", exc)

    async def _get_cached_usdc_balance(self) -> float | None:
        from config import ENABLE_REAL_LIVE_TRADING
        from storage_v2 import StorageV2
        now = time.time_ns()
        if not ENABLE_REAL_LIVE_TRADING:
            balance = StorageV2().get_simulated_balance(1000.0)
            self._balance_cache_usd = balance
            self._balance_cache_at_ns = now
            _persist_usdc_balance_snapshot(balance, now)
            return balance

        age_sec = (
            (now - self._balance_cache_at_ns) / 1e9
            if self._balance_cache_at_ns else float("inf")
        )
        if age_sec <= self.BALANCE_CACHE_TTL_SEC:
            return self._balance_cache_usd
        if self.client is None:
            self.client = LiveCLOBClient()
            
        # 2026-06-16 — REQUIRE STABILITY. The balance API lies. It returns
        # transient values ($102, $5.45) when the real balance is different.
        # Always read cash 3-6x and require stability before believing it.
        balances = []
        last_exc = None
        for _ in range(4):
            try:
                b = await self.client.get_usdc_balance()
                if b is not None:
                    balances.append(b)
            except Exception as exc:
                last_exc = exc
            if len(balances) >= 3 and max(balances[-3:]) - min(balances[-3:]) < 0.01:
                break
            await asyncio.sleep(0.5)
            
        balance = None
        if len(balances) >= 3 and max(balances[-3:]) - min(balances[-3:]) < 0.01:
            balance = balances[-1]
            
        if balance is None:
            if age_sec <= self.BALANCE_CACHE_STALE_MAX_SEC and self._balance_cache_usd is not None:
                logger.warning(
                    "[balance_gate] fetch failed or unstable (reads=%s): %s — using stale cache (age=%.1fs, bal=%.4f)",
                    balances, last_exc, age_sec, self._balance_cache_usd,
                )
                return self._balance_cache_usd
            logger.warning("[balance_gate] fetch failed or unstable (reads=%s): %s — no usable cache", balances, last_exc)
            return None
            
        self._balance_cache_usd = balance
        self._balance_cache_at_ns = now
        _persist_usdc_balance_snapshot(balance, now)
        return balance

    def set_session(self, session: aiohttp.ClientSession) -> None:
        self._http_session = session

    def _save(self):
        save_live_state(
            self.total_submitted_usd,
            self.total_filled_usd,
            self.open_positions,
            self.daily_realized_pnl_usd,
            self.last_reset_date,
            self._submitted_match_sides,
            self._submitted_match_usd,
            self._submitted_family_usd,
            mode=self._policy_mode(),
        )

    def add_realized_pnl(self, pnl_usd: float) -> None:
        self.daily_realized_pnl_usd += pnl_usd
        self._save()

    def decrement_open_positions(self, match_id: str | None = None, full_exit: bool = True):
        if self.open_positions > 0:
            self.open_positions -= 1
            self._save()
        if full_exit and match_id:
            if match_id in self._submitted_match_sides:
                del self._submitted_match_sides[match_id]
            if match_id in self._submitted_match_usd:
                del self._submitted_match_usd[match_id]

    def release_submitted_budget(
        self,
        order_usd: float,
        match_id: str | None = None,
        strategy_family: str | None = None,
    ) -> None:
        """Refund a submitted-but-unfilled order back to the available budget."""
        self.total_submitted_usd = max(0.0, self.total_submitted_usd - order_usd)
        if match_id and match_id in self._submitted_match_usd:
            self._submitted_match_usd[match_id] = max(0.0, self._submitted_match_usd[match_id] - order_usd)
            if self._submitted_match_usd[match_id] <= 0:
                del self._submitted_match_usd[match_id]
        if strategy_family and strategy_family in self._submitted_family_usd:
            self._submitted_family_usd[strategy_family] = max(
                0.0,
                self._submitted_family_usd[strategy_family] - order_usd,
            )
            if self._submitted_family_usd[strategy_family] <= 0:
                del self._submitted_family_usd[strategy_family]
        self._save()

    def remaining_budget(self) -> float:
        return max(0.0, MAX_TOTAL_LIVE_USD - self.total_submitted_usd)

    def _policy_mode(self) -> str:
        return "real_live" if ENABLE_REAL_LIVE_TRADING else "dry_live"

    def _risk_state_for_policy(self, match_id: str | None = None) -> dict[str, Any]:
        state = {
            "total_submitted_usd": self.total_submitted_usd,
            "open_positions": self.open_positions,
            "daily_realized_pnl_usd": self.daily_realized_pnl_usd,
            "match_open_usd": self._submitted_match_usd.get(str(match_id), 0.0) if match_id else None,
            "submitted_match_sides": self._submitted_match_sides.get(str(match_id)) if match_id else None,
            "submitted_family_usd": self._submitted_family_usd,
        }
        for family in ["VALUE", "EVENT", "DSWING", "MODEL_VALUE"]:
            state[f"{family}_max_live_usd"] = _strategy_family_cap_usd(family)
        return state

    def _apply_policy_result(self, attempt: LiveOrderAttempt, result: PolicyResult) -> LiveOrderAttempt:
        attempt.policy_allowed = result.allowed
        attempt.policy_reason = result.reason
        attempt.would_pass_live = result.would_pass_live
        attempt.live_skip_reason = result.live_skip_reason
        attempt.paper_only_bypass = result.paper_only_bypass
        attempt.policy_version = result.policy_version
        attempt.risk_tags = result.risk_tags
        if result.price_cap is not None and attempt.price_cap is None:
            attempt.price_cap = result.price_cap
        return attempt

    def _reject(self, signal: dict, mapping: dict, game: dict, reason: str, **extra) -> LiveOrderAttempt:
        policy_result = extra.get("policy_result") or result_for_existing_decision(
            False,
            reason,
            price_cap=extra.get("price_cap"),
            risk_tags=tuple(extra.get("risk_tags") or ()),
        )
        attempt = LiveOrderAttempt(
            event_type=str(signal.get("event_type") or ""),
            event_direction=str(signal.get("event_direction") or ""),
            token_id=str(signal.get("token_id") or ""),
            side=str(signal.get("side") or ""),
            fair_price=_to_float(signal.get("fair_price")),
            best_ask=_to_float(signal.get("ask")),
            price_cap=extra.get("price_cap"),
            edge=_to_float(signal.get("executable_edge")),
            lag=_to_float(signal.get("lag")),
            spread=_to_float(signal.get("spread")),
            book_age_ms=signal.get("book_age_ms"),
            steam_age_ms=signal.get("steam_age_ms"),
            order_type=LIVE_ORDER_TYPE,
            submitted_size_usd=0.0,
            event_quality=_to_float(signal.get("event_quality")),
            event_schema_version=signal.get("event_schema_version"),
            source_cadence_quality=signal.get("source_cadence_quality"),
            order_status="rejected_precheck",
            reason_if_rejected=reason,
            market_name=mapping.get("name"),
            match_id=str(game.get("match_id") or game.get("lobby_id") or ""),
            game_time_sec=game.get("game_time_sec"),
            created_at_ns=time.time_ns(),
            strategy_kind=extra.get("strategy_kind"),
            strategy_family=extra.get("strategy_family"),
            strategy_subtype=extra.get("strategy_subtype"),
            is_reversal=extra.get("is_reversal"),
            is_continuation=extra.get("is_continuation"),
        )
        return self._apply_policy_result(attempt, policy_result)

    async def _submit_buy_market(self, *, token_id: str, amount_usd: float, price_cap: float, tick_size: str, neg_risk: bool) -> dict[str, Any]:
        if LIVE_ORDER_TYPE == "FAK":
            return await self.client.buy_fak_market(
                token_id=token_id, amount_usd=amount_usd, price_cap=price_cap, tick_size=tick_size, neg_risk=neg_risk
            )
        if LIVE_ORDER_TYPE == "FOK":
            return await self.client.buy_fok_market(
                token_id=token_id, amount_usd=amount_usd, price_cap=price_cap, tick_size=tick_size, neg_risk=neg_risk
            )
        raise RuntimeError(f"Unsupported market order type: {LIVE_ORDER_TYPE}")

    async def try_buy(self, *, signal: dict, mapping: dict, game: dict, book_store) -> LiveOrderAttempt:
        mapping_result = validate_mapping_identity(mapping, game)
        if not mapping_result.ok:
            return self._reject(
                signal, mapping, game,
                f"mapping_invalid:{';'.join(mapping_result.mapping_errors) or 'confidence_not_1'}",
                risk_tags=("mapping_valid",),
            )
        token_id_for_policy = str(signal.get("token_id") or "")
        book_for_policy = book_store.get(token_id_for_policy) if book_store and token_id_for_policy else None
        signal_match_id = str(game.get("match_id") or game.get("lobby_id") or "")
        policy_result = evaluate_policy(
            PolicyInput(
                mode=self._policy_mode(),
                strategy_kind=str(signal.get("strategy_kind") or signal.get("event_family") or signal.get("event_type") or ""),
                market_type=str(mapping.get("market_type") or ""),
                token_id=token_id_for_policy,
                side=str(signal.get("side") or ""),
                signal=dict(signal),
                game=dict(game),
                mapping=dict(mapping),
                book=dict(book_for_policy) if book_for_policy else None,
                now_ns=time.time_ns(),
                risk_state=self._risk_state_for_policy(signal_match_id),
            )
        )
        if not policy_result.allowed:
            return self._reject(signal, mapping, game, policy_result.reason, policy_result=policy_result)

        if LIVE_ORDER_TYPE not in _ALLOWED_ORDER_TYPES:
            return self._reject(signal, mapping, game, "order_type_not_allowed")
        disk_reason = self.disk_guard.reject_reason()
        if disk_reason:
            return self._reject(signal, mapping, game, disk_reason)

        event_type = str(signal.get("event_type") or "")
        is_book_move = event_type == "BOOK_MOVE"

        token_id = str(signal.get("token_id") or "")
        book = book_store.get(token_id) if book_store else None
        if not book:
            return self._reject(signal, mapping, game, "missing_live_book")
        ask = _to_float(book.get("best_ask"))
        bid = _to_float(book.get("best_bid"))
        if ask is None or bid is None:
            return self._reject(signal, mapping, game, "missing_bid_or_ask")

        fair = _to_float(signal.get("fair_price"))
        lag = _to_float(signal.get("lag"))
        edge = _to_float(signal.get("executable_edge"))
        spread = ask - bid
        book_age = age_ms(book.get("received_at_ns"))
        steam_age = age_ms(game.get("received_at_ns"))
        event_max_fill = _to_float(signal.get("max_fill_price")) or DEFAULT_MAX_FILL_PRICE
        event_max_fill = min(max(event_max_fill, 0.0), 0.99)
        
        fresh_edge = (fair - ask) if fair is not None else 0.0

        tick_size = str(mapping.get("tick_size") or LIVE_TICK_SIZE)
        effective_ask = ask
        if is_book_move:
            _safety = 0.005
            price_cap = round_down_to_tick(fair - _safety, tick_size)
        else:
            # price_cap = marketable FAK ceiling. 2 ticks was too tight on thin/
            # moving books (ask rises as the favorite's signal fires → cap sits
            # below the live ask → zero fill). Widened+configurable; still bounded
            # by event_max_fill below and the S3 price gate (≤0.84).
            price_cap = round_down_to_tick(effective_ask + LIVE_FAK_BUFFER_TICKS * float(tick_size), tick_size)
        price_cap = min(price_cap, event_max_fill)
        price_cap = round_down_to_tick(price_cap, tick_size)
        if price_cap <= 0 or price_cap > 0.99 or not math.isfinite(price_cap):
            return self._reject(signal, mapping, game, "invalid_price_cap", price_cap=price_cap)
        _cap_ask = effective_ask if not is_book_move else ask
        if round_down_to_tick(_cap_ask, tick_size) > price_cap:
            return self._reject(signal, mapping, game, "best_ask_above_price_cap", price_cap=price_cap)

        # For GTC orders, post passively to skip the spread toll.
        # 2026-05-27: signal_execution_audit found buying at ask cost -2.1c/trade.
        # Posting at MID instead would flip -1.5c avg → +0.5c avg per trade.
        # Two modes:
        #   LIVE_GTC_ENTER_AT_MID=true  → post at (bid+ask)/2, clamped to [bid+tick, ask]
        #   LIVE_GTC_ENTER_AT_MID=false → post at fair_price (original behavior)
        gtc_limit_price: float | None = None
        if LIVE_ORDER_TYPE == "GTC" and not is_book_move:
            _tick = float(tick_size)
            if LIVE_GTC_ENTER_AT_MID and ask is not None and bid is not None:
                mid = (bid + ask) / 2.0
                _passive = round_down_to_tick(mid, tick_size)
            else:
                _passive = round_down_to_tick(fair, tick_size)
            _min_passive = round_down_to_tick(bid + _tick, tick_size)
            _max_passive = round_down_to_tick(ask, tick_size)  # never above ask (else == taker)
            gtc_limit_price = max(min(_passive, _max_passive), _min_passive)
            if gtc_limit_price <= 0 or not math.isfinite(gtc_limit_price):
                gtc_limit_price = None  # fall back to aggressive

        # B2: edge-weighted sizing. Bigger position on bigger edge, capped at
        # EDGE_SIZE_MAX_MULT (default 2.0). edge=MIN_EXECUTABLE_EDGE → 1x.
        edge_for_size = fresh_edge if fresh_edge > 0 else (edge or 0.0)
        size_mult = max(1.0, min(EDGE_SIZE_MAX_MULT, edge_for_size / max(MIN_EXECUTABLE_EDGE, 0.01)))

        # 2026-05-26 — premium-event sizing boost. When the trigger matches a
        # (event_type, feature, threshold) entry in PREMIUM_EVENT_FILTERS,
        # multiply trade size by PREMIUM_SIZE_MULT. Backtest n=21: +$12.90/trade
        # avg vs +$2.15 at flat sizing (6x improvement, 71% win, max DD 1%).
        premium_filter = PREMIUM_EVENT_FILTERS.get(event_type)
        if premium_filter is not None:
            feature_name, threshold = premium_filter
            if feature_name == "networth_delta_abs":
                feature_val = abs(_to_float(signal.get("networth_delta")) or 0.0)
            else:
                feature_val = _to_float(signal.get(feature_name)) or 0.0
            if feature_val >= threshold:
                size_mult *= PREMIUM_SIZE_MULT

        sized_trade_usd = MAX_TRADE_USD * size_mult

        # B1: per-match exposure cap. Limit cumulative USD on a single match so
        # high-frequency signals like VALUE_DISAGREEMENT can't stack to the moon.
        match_used = self._submitted_match_usd.get(signal_match_id, 0.0)
        match_remaining = max(0.0, MAX_OPEN_USD_PER_MATCH - match_used)

        order_usd = min(sized_trade_usd, self.remaining_budget(), match_remaining)
        if order_usd <= 0:
            reason = "no_remaining_live_budget"
            if match_remaining <= 0 and self.remaining_budget() > 0:
                reason = f"max_open_usd_per_match_reached:used={match_used:.2f}_cap={MAX_OPEN_USD_PER_MATCH:.2f}"
            return self._reject(signal, mapping, game, reason, price_cap=price_cap)

        # Dry-run shouldn't touch the CLOB at all — skip the balance fetch and
        # let the would_be_live_skipped branch below produce the attempt.
        if ENABLE_REAL_LIVE_TRADING:
            cached_balance = await self._get_cached_usdc_balance()
            if cached_balance is None:
                return self._reject(
                    signal, mapping, game,
                    "balance_fetch_failed_or_unstable",
                    price_cap=price_cap,
                )
            if cached_balance + 1e-6 < order_usd:
                return self._reject(
                    signal, mapping, game,
                    f"insufficient_balance_cached:bal={cached_balance:.4f}_need={order_usd:.4f}",
                    price_cap=price_cap,
                )

        neg_risk = bool(mapping.get("neg_risk", False))
        event_direction = str(signal.get("event_direction") or "")
        attempt = LiveOrderAttempt(
            event_type=event_type,
            event_direction=event_direction,
            token_id=token_id,
            side=str(signal.get("side") or ""),
            fair_price=fair,
            best_ask=_cap_ask,
            price_cap=price_cap,
            edge=round(fresh_edge, 4),
            lag=lag,
            spread=round(spread, 4),
            book_age_ms=book_age,
            steam_age_ms=steam_age,
            order_type=LIVE_ORDER_TYPE,
            submitted_size_usd=order_usd,
            event_quality=_to_float(signal.get("event_quality")),
            event_schema_version=signal.get("event_schema_version"),
            source_cadence_quality=signal.get("source_cadence_quality"),
            market_name=mapping.get("name"),
            match_id=str(game.get("match_id") or game.get("lobby_id") or ""),
            game_time_sec=game.get("game_time_sec"),
            created_at_ns=time.time_ns(),
            strategy_kind=signal.get("strategy_kind") or signal.get("event_family") or signal.get("event_type"),
            strategy_family=signal.get("strategy_family"),
            strategy_subtype=signal.get("strategy_subtype"),
            is_reversal=bool(signal.get("is_reversal", False)),
            is_continuation=bool(signal.get("is_continuation", False)),
        )
        self._apply_policy_result(
            attempt,
            result_for_existing_decision(
                True,
                "allowed",
                price_cap=price_cap,
                size_usd=order_usd,
                risk_tags=policy_result.risk_tags,
            ),
        )

        attempt.submit_start_ns = time.time_ns()
        if not ENABLE_REAL_LIVE_TRADING:
            attempt.response_received_ns = time.time_ns()
            attempt.order_status = "filled"
            attempt.reason_if_rejected = "paper_simulated"
            attempt.order_id = f"paper_entry_{time.time_ns()}"
            attempt.filled_size_usd = round(order_usd, 6)
            attempt.avg_fill_price = attempt.best_ask
            self.total_submitted_usd += order_usd
            self.total_filled_usd += attempt.filled_size_usd
            self.open_positions += 1
            self._save()
            return attempt

        # Real trading attempt: ensure client is loaded
        if self.client is None:
            self.client = LiveCLOBClient()

        try:
            if LIVE_ORDER_TYPE == "GTC":
                resp = await self.client.buy_gtc_limit(
                    token_id=token_id,
                    amount_usd=order_usd,
                    price_cap=price_cap,
                    tick_size=tick_size,
                    neg_risk=neg_risk,
                    limit_price=gtc_limit_price,
                )
            else:
                resp = await self._submit_buy_market(
                    token_id=token_id,
                    amount_usd=order_usd,
                    price_cap=price_cap,
                    tick_size=tick_size,
                    neg_risk=neg_risk,
                )
            attempt.response_received_ns = time.time_ns()
        except Exception as exc:
            attempt.response_received_ns = time.time_ns()
            attempt.order_status = "exception"
            attempt.reason_if_rejected = repr(exc)
            if attempt.submit_start_ns and attempt.response_received_ns:
                attempt.submit_latency_ms = round((attempt.response_received_ns - attempt.submit_start_ns) / 1_000_000, 2)
            return attempt

        if attempt.submit_start_ns and attempt.response_received_ns:
            attempt.submit_latency_ms = round((attempt.response_received_ns - attempt.submit_start_ns) / 1_000_000, 2)

        attempt.raw_response_json = json.dumps(_jsonable(resp), sort_keys=True)[:4000]
        attempt.order_status = _status_from_response(resp)
        attempt.reason_if_rejected = _error_from_response(resp)
        attempt.order_id = _order_id_from_response(resp)
        attempt.filled_size_usd = round(_filled_usd_from_response(resp, order_usd), 6)
        actual_order_price = gtc_limit_price if gtc_limit_price is not None else price_cap
        attempt.avg_fill_price = _avg_fill_price(resp, actual_order_price, attempt.filled_size_usd)

        if attempt.filled_size_usd > 0 or attempt.order_status in ("delayed", "live"):
            # Consume budget if order was accepted by the sequencer (filled or pending)
            self.total_submitted_usd += order_usd
            self.total_filled_usd += attempt.filled_size_usd
            self.open_positions += 1
            if signal_match_id and event_direction:
                # Track directions as a list to allow opposite-side bets on the
                # same match. Each unique direction can fire once; same-direction
                # retries are blocked at the entry gate above.
                existing = self._submitted_match_sides.get(signal_match_id)
                if isinstance(existing, list):
                    if event_direction not in existing:
                        existing.append(event_direction)
                elif isinstance(existing, set):
                    existing.add(event_direction)
                    self._submitted_match_sides[signal_match_id] = list(existing)
                elif existing:
                    # Legacy single-string format — migrate to list
                    if existing != event_direction:
                        self._submitted_match_sides[signal_match_id] = [existing, event_direction]
                else:
                    self._submitted_match_sides[signal_match_id] = [event_direction]
                # B1: track per-match cumulative submitted USD.
                self._submitted_match_usd[signal_match_id] = self._submitted_match_usd.get(signal_match_id, 0.0) + order_usd
            self._save()

            # Delayed orders are queued by the sequencer and may never fill.
            # Poll for confirmation; cancel and release budget if still pending at 30s.
            if attempt.order_status == "delayed" and attempt.order_id:
                asyncio.ensure_future(
                    self._poll_and_cancel_delayed(
                        order_id=attempt.order_id,
                        order_usd=order_usd,
                        match_id=signal_match_id,
                        attempt=attempt,
                    )
                )

        return attempt

    def _reject_value(self, signal, mapping, game, token_id, size_usd, reason, policy_result: PolicyResult | None = None) -> LiveOrderAttempt:
        signal_kind = signal.__class__.__name__
        if signal_kind == "EventTriggeredValueSignal":
            event_type = "EVENT_REVERSAL_EDGE" if signal.is_reversal else "EVENT_CONTINUATION_EDGE"
            strategy_family = "EVENT"
            strategy_subtype = signal.actual_event_type
            is_reversal = signal.is_reversal
            is_continuation = signal.is_continuation
        elif signal_kind == "DSwingSignal":
            event_type = "DSWING"
            strategy_family = "DSWING"
            strategy_subtype = None
            is_reversal = False
            is_continuation = False
        else:
            event_type = "VALUE_EDGE"
            strategy_family = "VALUE"
            strategy_subtype = None
            is_reversal = False
            is_continuation = False
            
        trader_kind = "dswing" if event_type == "DSWING" else "value"

        policy_result = policy_result or result_for_existing_decision(
            False,
            reason,
            size_usd=size_usd,
        )
        attempt = LiveOrderAttempt(
            event_type=event_type,
            event_direction=str(signal.direction),
            token_id=str(token_id or ""),
            side=signal.side,
            fair_price=signal.fair_price,
            best_ask=None, price_cap=None, edge=signal.edge, lag=None, spread=None,
            book_age_ms=signal.book_age_ms, steam_age_ms=None,
            order_type="FAK",
            submitted_size_usd=0.0,
            order_status="rejected_precheck",
            reason_if_rejected=reason,
            market_name=mapping.get("name"),
            match_id=str(game.get("match_id") or game.get("lobby_id") or ""),
            game_time_sec=signal.game_time_sec,
            created_at_ns=time.time_ns(),
            trader_kind=trader_kind,
            exit_horizon_sec=None,
            signal_id=signal.signal_id,
            strategy_kind=event_type,
            strategy_family=strategy_family,
            strategy_subtype=strategy_subtype,
            is_reversal=is_reversal,
            is_continuation=is_continuation,
            model_version=getattr(signal, "model_version", None),
            token_net_worth_lead=getattr(signal, "token_net_worth_lead", None),
            token_score_margin=getattr(signal, "token_score_margin", None),
            radiant_net_worth=getattr(signal, "radiant_net_worth", None),
            dire_net_worth=getattr(signal, "dire_net_worth", None),
            radiant_score=getattr(signal, "radiant_score", None),
            dire_score=getattr(signal, "dire_score", None),
        )
        return self._apply_policy_result(attempt, policy_result)

    async def try_buy_manual(
        self,
        *,
        signal: dict,
        mapping: dict,
        token_id: str,
        match_id: str,
        book_store,
    ) -> LiveOrderAttempt:
        """Submit a FAK buy from a dashboard manual order."""
        # TODO: P1 follow-up: create manual_order_policy.py for manual entry safety checks.
        # Manual orders intentionally do not use evaluate_policy().
        # They are operator-directed actions and require a separate manual-order safety policy.
        # Do not route them through automated event/cadence/strategy gates.
        def _reject(reason: str) -> LiveOrderAttempt:
            attempt = LiveOrderAttempt(
                event_type="MANUAL",
                event_direction="manual",
                token_id=str(token_id),
                side=signal.get("side", "YES"),
                fair_price=0.0, best_ask=None, price_cap=None, edge=0.0, lag=None, spread=None,
                book_age_ms=0, steam_age_ms=None,
                order_type="FAK", submitted_size_usd=0.0,
                order_status="rejected_precheck", reason_if_rejected=reason,
                market_name=mapping.get("name"), match_id=str(match_id),
                game_time_sec=0, created_at_ns=time.time_ns(),
                trader_kind="manual", exit_horizon_sec=None,
                strategy_kind="MANUAL", strategy_family="MANUAL", strategy_subtype=None,
                is_reversal=False, is_continuation=False,
                manual_operator=signal.get("operator", "unknown_operator"),
                manual_source=signal.get("source", "dashboard_manual"),
                manual_pre_trade_book=json.dumps(_jsonable(book_store.get_book(token_id))),
            )
            return self._apply_policy_result(
                attempt,
                result_for_existing_decision(False, reason or "rejected"),
            )

        from config import ENABLE_REAL_LIVE_TRADING, LIVE_ORDER_TYPE
        if LIVE_ORDER_TYPE != "FAK":
            return _reject(f"manual_orders_only_support_FAK_found_{LIVE_ORDER_TYPE}")

        if not ENABLE_REAL_LIVE_TRADING:
            return _reject("ENABLE_REAL_LIVE_TRADING=false")

        book = book_store.get_book(token_id)
        if not book: return _reject("no_book")
        ask = book.get("best_ask")
        if ask is None: return _reject("no_ask")

        price_cap = signal.get("price_cap")
        if price_cap is None:
            price_cap = round(min(ask + 0.04, 0.99), 4)

        size_usd = float(signal.get("size_usd", 0.0))
        from manual_order_policy import evaluate_manual_policy
        match_used = self._submitted_match_usd.get(match_id, 0.0)
        
        policy_result = evaluate_manual_policy(
            size_usd=size_usd,
            match_used_usd=match_used,
            total_submitted_usd=self.total_submitted_usd,
            open_positions=self.open_positions,
            daily_realized_pnl_usd=self.daily_realized_pnl_usd,
            remaining_budget_usd=self.remaining_budget(),
            token_id=token_id,
            mapping=mapping,
            book=book,
            ask=ask,
            price_cap=price_cap,
            operator=signal.get("operator", "unknown_operator"),
            source=signal.get("source", "dashboard_manual"),
            pre_trade_book=book,
        )

        if not policy_result.allowed:
            return _reject(policy_result.reason)

        usdc = await self._get_cached_usdc_balance()
        if usdc is None:
            return _reject("balance_fetch_failed_or_unstable")
        if usdc < size_usd:
            return _reject("insufficient_balance")

        attempt = _reject(None)
        attempt.order_status = "submitting"
        attempt.submitted_size_usd = size_usd
        attempt.best_ask = ask
        attempt.price_cap = price_cap
        self._apply_policy_result(
            attempt,
            result_for_existing_decision(True, "allowed", price_cap=price_cap, size_usd=size_usd),
        )

        tick = mapping.get("tick_size") or str(LIVE_TICK_SIZE)
        neg_risk = bool(mapping.get("neg_risk", False))

        try:
            if self.client is None:
                self.client = LiveCLOBClient()
            resp = await self.client.buy_fak_market(
                token_id=token_id, amount_usd=size_usd, price_cap=price_cap, tick_size=tick, neg_risk=neg_risk
            )
            attempt.order_id = _order_id_from_response(resp)
            attempt.order_status = _status_from_response(resp)
            attempt.reason_if_rejected = _error_from_response(resp) if attempt.order_status in ("exception", "error", "rejected") else None
            
            filled_usd = _filled_usd_from_response(resp, size_usd)
            attempt.filled_size_usd = filled_usd if filled_usd > 0 else None
            attempt.avg_fill_price = _avg_fill_price(resp, price_cap, filled_usd)
            
            if attempt.order_status in ("delayed", "live"):
                self.total_submitted_usd += size_usd
                self._submitted_match_usd[match_id] = self._submitted_match_usd.get(match_id, 0.0) + size_usd
                asyncio.create_task(self._poll_and_cancel_delayed(
                    order_id=attempt.order_id, token_id=token_id, match_id=match_id,
                    submitted_usd=size_usd, attempt=attempt
                ))
            elif attempt.order_status == "filled" or (attempt.order_status in ("partial", "killed") and filled_usd > 0.01):
                self.total_submitted_usd += size_usd
                self.total_filled_usd += filled_usd
                self._submitted_match_usd[match_id] = self._submitted_match_usd.get(match_id, 0.0) + size_usd
                self.open_positions += 1
            
            self._save()
        except Exception as exc:
            attempt.order_status = "exception"
            attempt.reason_if_rejected = str(exc)[:80]
            
        return attempt

    async def try_buy_value(
        self,
        *,
        signal: Any,            # value_engine.ValueSignal
        mapping: dict,
        game: dict,
        book_store,
    ) -> LiveOrderAttempt:
        """Submit a FAK buy from a ValueSignal (winprob value bot, hold-to-settle).

        The value engine carries ALL its gates upstream (min lead, min edge, max
        price, book freshness, and the orientation-flip guard), so here we only
        check budget, compute the FAK price cap, and submit. No spread gate and no
        exit horizon: the edge is informational and the position is held to
        settlement (live_exit_engine trader_kind='value' exits only on game_over /
        max-hold). Re-entry cooldown prevents stacking the same match every tick.
        """
        signal_match_id = str(game.get("match_id") or game.get("lobby_id") or "")
        token_id = signal.token_id or (
            mapping.get("yes_token_id") if signal.side == "YES" else mapping.get("no_token_id"))
        if not token_id:
            return self._reject_value(signal, mapping, game, "", 0.0, "missing_token_id")

        size_usd = min(float(signal.sized_usd), MAX_TRADE_USD)
        (
            attempt_event_type,
            strategy_family,
            strategy_subtype,
            is_reversal,
            is_continuation,
        ) = _value_signal_strategy_meta(signal)

        mapping_result = validate_mapping_identity(mapping, game)
        if not mapping_result.ok:
            return self._reject_value(
                signal, mapping, game, token_id, size_usd,
                f"mapping_invalid:{';'.join(mapping_result.mapping_errors) or 'confidence_not_1'}"
            )

        if attempt_event_type == "VALUE_EDGE":
            policy_event_type = "VALUE"
        elif attempt_event_type in {"EVENT_CONTINUATION_EDGE", "EVENT_REVERSAL_EDGE"}:
            policy_event_type = "EVENT_TRIGGERED_VALUE"
        else:
            policy_event_type = attempt_event_type

        signal_dict = {
            "event_type": policy_event_type,
            "strategy_kind": attempt_event_type,
            "strategy_family": strategy_family,
            "strategy_subtype": strategy_subtype,
            "event_direction": str(signal.direction),
            "token_id": token_id,
            "side": signal.side,
            "fair_price": signal.fair_price,
            "executable_edge": signal.edge,
            "size_usd": float(signal.sized_usd),
            "book_age_ms": signal.book_age_ms,
            "game_time_sec": signal.game_time_sec,
            "is_reversal": is_reversal,
            "is_continuation": is_continuation,
            "target_horizon": getattr(signal, "target_horizon", None),
            "expected_hold_sec": getattr(signal, "expected_hold_sec", None),
        }
        book_for_policy = book_store.get(token_id) if book_store else None
        policy_result = evaluate_policy(
            PolicyInput(
                mode=self._policy_mode(),
                strategy_kind=attempt_event_type,
                market_type=str(mapping.get("market_type") or ""),
                token_id=token_id,
                side=str(signal.side),
                signal=signal_dict,
                game=dict(game),
                mapping=dict(mapping),
                book=dict(book_for_policy) if book_for_policy else None,
                now_ns=time.time_ns(),
                risk_state=self._risk_state_for_policy(signal_match_id),
            )
        )
        if not policy_result.allowed:
            return self._reject_value(signal, mapping, game, token_id, size_usd, policy_result.reason, policy_result=policy_result)

        disk_reason = self.disk_guard.reject_reason()
        if disk_reason:
            return self._reject_value(signal, mapping, game, token_id, size_usd, disk_reason)

        # Re-entry cooldown per (match, direction). Hold-to-settle wants ONE bet
        # per match-side; the cooldown plus the caller's open-position check guard
        # against re-buying every poll.
        cd_key = f"value|{signal_match_id}|{signal.direction}"
        _cool = max(VALUE_REENTRY_COOLDOWN_SEC, 60)
        last_ns = self._value_last_entry_ns.get(cd_key, 0)
        if last_ns and (time.time_ns() - last_ns) / 1e9 < _cool:
            return self._reject_value(signal, mapping, game, token_id, size_usd, "value_cooldown")

        # --- book lookup ---
        book = book_store.get(token_id) if book_store is not None else None
        ask = _to_float(book.get("best_ask")) if book else None
        if ask is None:
            return self._reject_value(signal, mapping, game, token_id, size_usd, "missing_ask")

        tick_size = str(mapping.get("tick_size") or LIVE_TICK_SIZE)
        try:
            _tick = float(tick_size)
        except ValueError:
            _tick = 0.01
        # FAK ceiling. ask+2 ticks was too tight on thin/moving favorite books (the ask
        # rises as the leader's signal fires -> cap below the live ask -> 0 fill; that's
        # why VALUE was 0/16 while the event path, already on 4 ticks, fills ~54%). Match
        # the event path's proven buffer. Bounded below by fair (never pay past fair).
        price_cap = round(min(ask + VALUE_FAK_BUFFER_TICKS * _tick, signal.fair_price), 4)
        neg_risk = bool(mapping.get("neg_risk", False))

        if ask > price_cap:
            return self._reject_value(
                signal, mapping, game, token_id, size_usd,
                f"best_ask_above_price_cap:ask={ask:.4f}_cap={price_cap:.4f}"
            )

        if ask > signal.fair_price - 0.005:
            return self._reject_value(
                signal, mapping, game, token_id, size_usd,
                f"execution_price_protection_no_edge:ask={ask:.4f}_fair={signal.fair_price:.4f}"
            )

        from config import LIVE_ORDER_TYPE
        if LIVE_ORDER_TYPE not in {"FAK", "FOK"}:
            return self._reject_value(signal, mapping, game, token_id, size_usd, f"unsupported_order_type:{LIVE_ORDER_TYPE}")

        trader_kind = "dswing" if attempt_event_type == "DSWING" else "value"

        attempt = LiveOrderAttempt(
            event_type=attempt_event_type,
            event_direction=str(signal.direction),
            token_id=str(token_id),
            side=signal.side,
            fair_price=signal.fair_price,
            best_ask=ask,
            price_cap=price_cap,
            edge=signal.edge,
            lag=None,
            spread=None,
            book_age_ms=signal.book_age_ms,
            steam_age_ms=None,
            order_type=LIVE_ORDER_TYPE,
            submitted_size_usd=size_usd,
            market_name=mapping.get("name"),
            match_id=signal_match_id,
            game_time_sec=signal.game_time_sec,
            created_at_ns=time.time_ns(),
            trader_kind=trader_kind,
            exit_horizon_sec=None,
            signal_id=signal.signal_id,
            strategy_kind=attempt_event_type,
            strategy_family=strategy_family,
            strategy_subtype=strategy_subtype,
            is_reversal=is_reversal,
            is_continuation=is_continuation,
            model_version=getattr(signal, "model_version", None),
            token_net_worth_lead=getattr(signal, "token_net_worth_lead", None),
            token_score_margin=getattr(signal, "token_score_margin", None),
            radiant_net_worth=getattr(signal, "radiant_net_worth", None),
            dire_net_worth=getattr(signal, "dire_net_worth", None),
            radiant_score=getattr(signal, "radiant_score", None),
            dire_score=getattr(signal, "dire_score", None),
        )
        self._apply_policy_result(
            attempt,
            result_for_existing_decision(
                True,
                "allowed",
                price_cap=price_cap,
                size_usd=size_usd,
            ),
        )

        attempt.submit_start_ns = time.time_ns()
        if not ENABLE_REAL_LIVE_TRADING:
            attempt.response_received_ns = time.time_ns()
            attempt.order_status = "filled"
            attempt.reason_if_rejected = "paper_simulated"
            prefix = "paper_event_value" if attempt_event_type == "EVENT_TRIGGERED_VALUE" else "paper_value"
            attempt.order_id = f"{prefix}_{time.time_ns()}"
            attempt.filled_size_usd = round(size_usd, 6)
            attempt.avg_fill_price = attempt.best_ask
            self.total_submitted_usd += size_usd
            self.total_filled_usd += attempt.filled_size_usd
            self.open_positions += 1
            self._submitted_match_usd[signal_match_id] = self._submitted_match_usd.get(signal_match_id, 0.0) + size_usd
            self._submitted_family_usd[strategy_family] = self._submitted_family_usd.get(strategy_family, 0.0) + size_usd
            self._value_last_entry_ns[cd_key] = time.time_ns()
            self._save()
            return attempt

        if self.client is None:
            self.client = LiveCLOBClient()

        try:
            resp = await self._submit_buy_market(
                token_id=str(token_id),
                amount_usd=float(size_usd),
                price_cap=float(price_cap),
                tick_size=str(tick_size),
                neg_risk=bool(neg_risk),
            )
            attempt.response_received_ns = time.time_ns()
        except Exception as exc:
            attempt.response_received_ns = time.time_ns()
            attempt.order_status = "exception"
            attempt.reason_if_rejected = repr(exc)
            if attempt.submit_start_ns and attempt.response_received_ns:
                attempt.submit_latency_ms = round(
                    (attempt.response_received_ns - attempt.submit_start_ns) / 1_000_000, 2)
            return attempt

        if attempt.submit_start_ns and attempt.response_received_ns:
            attempt.submit_latency_ms = round(
                (attempt.response_received_ns - attempt.submit_start_ns) / 1_000_000, 2)

        attempt.raw_response_json = json.dumps(_jsonable(resp), sort_keys=True)[:4000]
        attempt.order_status = _status_from_response(resp)
        attempt.reason_if_rejected = _error_from_response(resp)
        attempt.order_id = _order_id_from_response(resp)
        attempt.filled_size_usd = round(_filled_usd_from_response(resp, size_usd), 6)
        attempt.avg_fill_price = _avg_fill_price(resp, price_cap, attempt.filled_size_usd)
        if attempt.avg_fill_price is None and attempt.order_status in ("delayed", "live", "filled", "matched"):
            attempt.avg_fill_price = price_cap

        if attempt.filled_size_usd > 0 or attempt.order_status in ("delayed", "live"):
            self.total_submitted_usd += size_usd
            self.total_filled_usd += attempt.filled_size_usd
            self.open_positions += 1
            self._submitted_match_usd[signal_match_id] = self._submitted_match_usd.get(signal_match_id, 0.0) + size_usd
            self._submitted_family_usd[strategy_family] = self._submitted_family_usd.get(strategy_family, 0.0) + size_usd
            self._value_last_entry_ns[cd_key] = time.time_ns()
            self._save()

        # Marketable GTC rests after Polymarket's ~1s delay — poll for fill; cancel + release at 30s.
        if attempt.order_status in ("delayed", "live") and attempt.order_id:
            asyncio.ensure_future(
                self._poll_and_cancel_delayed(
                    order_id=attempt.order_id, order_usd=size_usd,
                    match_id=signal_match_id, strategy_family=strategy_family, attempt=attempt,
                )
            )

        return attempt

    async def _poll_and_cancel_delayed(
        self,
        *,
        order_id: str,
        order_usd: float,
        match_id: str,
        strategy_family: str | None = None,
        attempt: LiveOrderAttempt | None = None,
    ) -> None:
        """Poll a delayed FAK order and cancel it if still unfilled after 30s."""
        _POLL_SCHEDULE = (5, 15, 30)  # seconds after submission
        elapsed = 0
        for target in _POLL_SCHEDULE:
            await asyncio.sleep(target - elapsed)
            elapsed = target
            try:
                status = await self._poll_order_status(order_id)
            except Exception as exc:
                logger.warning("[delayed_poll] order=%s status check failed: %s", order_id, exc)
                continue

            order_status = status.get("status") or status.get("order_status") or ""
            # "size_matched" semantics are field-dependent (shares vs USD); only
            # credit total_filled_usd from the explicitly-named USD field.
            confirmed_filled_usd = float(status.get("filled_size_usd") or 0)
            heuristic_filled = float(status.get("size_matched") or 0)

            if order_status in ("filled", "matched") or heuristic_filled >= order_usd * 0.9:
                # Credit the actual filled USD if the CLOB returned it; otherwise
                # fall back to assuming a full fill (the heuristic above passed,
                # so we know it filled at minimum 90%).
                credited_usd = confirmed_filled_usd if confirmed_filled_usd > 0 else order_usd
                self.total_filled_usd += credited_usd
                self._save()
                if attempt is not None:
                    attempt.order_status = "filled"
                    attempt.filled_size_usd = round(credited_usd, 6)
                    attempt.raw_response_json = json.dumps(_jsonable(status), sort_keys=True)[:4000]
                logger.info(
                    "[delayed_poll] order=%s filled at t=%ss — credited filled_usd=%.4f",
                    order_id, elapsed, credited_usd,
                )
                await self._emit_delayed_resolution(attempt)
                return

            if order_status in ("cancelled", "canceled", "expired", "rejected"):
                logger.info("[delayed_poll] order=%s already %s — releasing budget", order_id, order_status)
                if attempt is not None:
                    attempt.order_status = order_status
                    attempt.reason_if_rejected = f"delayed_order_{order_status}"
                    attempt.raw_response_json = json.dumps(_jsonable(status), sort_keys=True)[:4000]
                    if confirmed_filled_usd > 0:
                        attempt.filled_size_usd = round(confirmed_filled_usd, 6)
                
                amount_to_release = max(0.0, order_usd - confirmed_filled_usd)
                if confirmed_filled_usd > 0:
                    self.total_filled_usd += confirmed_filled_usd
                    self._save()

                self.release_submitted_budget(amount_to_release, match_id=match_id, strategy_family=strategy_family)
                self.decrement_open_positions(match_id, full_exit=False)
                await self._emit_delayed_resolution(attempt)
                return

        # Still pending after 30s — cancel and release
        logger.warning("[delayed_poll] order=%s still pending at 30s — cancelling", order_id)
        
        # We need the last known confirmed_filled_usd from the loop
        confirmed_filled_usd = 0.0
        try:
            # Re-fetch one last time to ensure we have the most accurate filled amount before canceling
            final_status = await self._poll_order_status(order_id)
            confirmed_filled_usd = float(final_status.get("filled_size_usd") or 0)
        except Exception:
            pass

        try:
            if self.client is None:
                self.client = LiveCLOBClient()
            await self.client.cancel_order_by_id(order_id)
        except Exception as exc:
            logger.warning("[delayed_poll] cancel order=%s failed: %s. NOT releasing budget to prevent leak.", order_id, exc)
            return

        if attempt is not None:
            attempt.order_status = "cancelled"
            attempt.reason_if_rejected = "delayed_order_timeout_cancelled"
            if confirmed_filled_usd > 0:
                attempt.filled_size_usd = round(confirmed_filled_usd, 6)
                
        amount_to_release = max(0.0, order_usd - confirmed_filled_usd)
        if confirmed_filled_usd > 0:
            self.total_filled_usd += confirmed_filled_usd
            self._save()

        self.release_submitted_budget(amount_to_release, match_id=match_id, strategy_family=strategy_family)
        self.decrement_open_positions(match_id, full_exit=False)
        await self._emit_delayed_resolution(attempt)

    async def _poll_order_status(self, order_id: str) -> dict:
        if self.client is None:
            self.client = LiveCLOBClient()
        try:
            resp = await self.client.get_order_status(order_id)
            return _response_to_dict(resp) if isinstance(resp, dict) else {"raw": str(resp)}
        except Exception as exc:
            return {"error": repr(exc)}
