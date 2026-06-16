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
)
import aiohttp
from event_taxonomy import PREMIUM_EVENT_FILTERS, event_tier
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

# Continuous-engine entry gates (not in the scorer because they depend on the
# live book at submit time, not on the snapshot pair).
CONTINUOUS_MAX_SPREAD = float(os.getenv("CONTINUOUS_MAX_SPREAD", "0.03"))
CONTINUOUS_REENTRY_COOLDOWN_SEC = float(os.getenv("CONTINUOUS_REENTRY_COOLDOWN_SEC", "60"))

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
        parent = os.path.dirname(_USDC_BALANCE_PATH)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(_USDC_BALANCE_PATH, "w", encoding="utf-8") as f:
            json.dump({"usdc_balance": float(balance), "checked_at_ns": int(at_ns)}, f)
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
    # 2026-05-29 Phase CS-3 — trader_kind distinguishes the three strategy paths.
    # "event" = legacy event-detector path (default, kept for back-compat).
    # "continuous" = continuous_engine.
    # "scalp"  = scalp_executor.
    # "arb"    = arb scanner (Phase AR).
    trader_kind: str = "event"
    # Continuous (and arb) positions carry their own fixed-horizon exit timer.
    # Event path leaves this None and falls back to the legacy exit-engine logic.
    exit_horizon_sec: int | None = None
    signal_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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

        if bid is None:
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
        raw_bid_price = min(round_down_to_tick(float(bid), tick_size), max_price)
        bid_price = raw_bid_price
        
        if MAKER_EXIT_MODE:
            # Aggressive maker: post at best_bid + 1 tick to be top of book
            maker_price = min(round_down_to_tick(bid_price + tick, tick_size), max_price)
            # But don't exceed current ask (if we do, we might as well just hit the bid or ask)
            # Actually, if we post at best_ask, we are joining the queue.
            # If we post at best_bid + tick, we are the new best ask.
            if ask is not None:
                ask_price = min(round_down_to_tick(float(ask), tick_size), max_price)
                # If spread is 1 tick, maker_price == ask_price.
                # If spread > 1 tick, maker_price < ask_price.
                bid_price = min(maker_price, ask_price)
            else:
                bid_price = maker_price

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
        state = load_live_state()
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
            self._save()
        else:
            self._submitted_match_sides = state.get("submitted_match_sides", {})
            self._submitted_match_usd = state.get("submitted_match_usd", {})

        # In-memory only — per-direction re-entry cooldown for continuous engine.
        # Key: "<match_id>|<direction>". Value: last_entry_time_ns.
        self._continuous_last_entry_ns: dict[str, int] = {}

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
        now = time.time_ns()
        age_sec = (
            (now - self._balance_cache_at_ns) / 1e9
            if self._balance_cache_at_ns else float("inf")
        )
        if age_sec <= self.BALANCE_CACHE_TTL_SEC:
            return self._balance_cache_usd
        if self.client is None:
            self.client = LiveCLOBClient()
        try:
            balance = await self.client.get_usdc_balance()
        except Exception as exc:
            if age_sec <= self.BALANCE_CACHE_STALE_MAX_SEC and self._balance_cache_usd is not None:
                logger.warning(
                    "[balance_gate] fetch failed: %s — using stale cache (age=%.1fs, bal=%.4f)",
                    exc, age_sec, self._balance_cache_usd,
                )
                return self._balance_cache_usd
            logger.warning("[balance_gate] fetch failed: %s — no usable cache", exc)
            return None
        if balance is not None:
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
        )

    def add_realized_pnl(self, pnl_usd: float) -> None:
        self.daily_realized_pnl_usd += pnl_usd
        self._save()

    def decrement_open_positions(self, match_id: str | None = None):
        if self.open_positions > 0:
            self.open_positions -= 1
            self._save()
        if match_id and match_id in self._submitted_match_sides:
            del self._submitted_match_sides[match_id]
        if match_id and match_id in self._submitted_match_usd:
            del self._submitted_match_usd[match_id]

    def release_submitted_budget(self, order_usd: float, match_id: str | None = None) -> None:
        """Refund a submitted-but-unfilled order back to the available budget."""
        self.total_submitted_usd = max(0.0, self.total_submitted_usd - order_usd)
        if match_id and match_id in self._submitted_match_usd:
            self._submitted_match_usd[match_id] = max(0.0, self._submitted_match_usd[match_id] - order_usd)
            if self._submitted_match_usd[match_id] <= 0:
                del self._submitted_match_usd[match_id]
        self._save()

    def remaining_budget(self) -> float:
        return max(0.0, MAX_TOTAL_LIVE_USD - self.total_submitted_usd)

    def _reject(self, signal: dict, mapping: dict, game: dict, reason: str, **extra) -> LiveOrderAttempt:
        return LiveOrderAttempt(
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
        )

    async def try_buy(self, *, signal: dict, mapping: dict, game: dict, book_store) -> LiveOrderAttempt:
        mapping_result = validate_mapping_identity(mapping, game)
        if not mapping_result.ok:
            return self._reject(
                signal, mapping, game,
                f"mapping_invalid:{';'.join(mapping_result.mapping_errors) or 'confidence_not_1'}",
            )
        if not ALLOW_EVENT_TRADES:
            return self._reject(signal, mapping, game, "event_trades_disabled")
        if ALLOW_GAME_OVER_ONLY and not game.get("game_over"):
            return self._reject(signal, mapping, game, "game_over_only")
        if LIVE_ORDER_TYPE not in _ALLOWED_ORDER_TYPES:
            return self._reject(signal, mapping, game, "order_type_not_allowed")
        if self.total_submitted_usd >= MAX_TOTAL_LIVE_USD:
            return self._reject(signal, mapping, game, "max_total_live_usd_reached")
        if self.open_positions >= MAX_OPEN_POSITIONS:
            return self._reject(signal, mapping, game, "max_open_positions_reached")
        if self.daily_realized_pnl_usd <= -MAX_DAILY_DRAWDOWN_USD:
            return self._reject(signal, mapping, game, f"daily_drawdown_circuit_breaker:{self.daily_realized_pnl_usd:.2f}")
        disk_reason = self.disk_guard.reject_reason()
        if disk_reason:
            return self._reject(signal, mapping, game, disk_reason)

        # 2026-06-03 — ORIENTATION SANITY GUARD. The binder occasionally flips the
        # token↔team mapping (yes_token bound to the wrong outcome — confirmed on
        # GLYPH vs Carstensz). A flip is catastrophic AND invisible to the value
        # gate (buying the LOSER cheap looks like a perfect value buy). Block any
        # trade where the yes_token price grossly contradicts yes_team's net-worth
        # standing. Conservative thresholds so genuine value trades aren't caught.
        _rl = _to_float(game.get("radiant_lead"))
        if _rl is not None:
            _yes_team = mapping.get("yes_team")
            _srt = (mapping.get("steam_radiant_team") or "").strip()
            _yes_is_rad = (_srt == _yes_team) if _srt else (game.get("radiant_team") == _yes_team)
            _yes_lead = _rl if _yes_is_rad else -_rl
            _yb = book_store.get(mapping.get("yes_token_id")) if book_store else None
            _yp = _to_float((_yb or {}).get("best_ask"))
            if _yp is not None and ((_yes_lead > 5000 and _yp < 0.35) or (_yes_lead < -5000 and _yp > 0.65)):
                return self._reject(signal, mapping, game,
                    f"orientation_flip_suspected:yes_lead={_yes_lead:.0f}_yes_ask={_yp:.2f}")

        event_type = str(signal.get("event_type") or "")
        cluster_types = {e for e in str(signal.get("cluster_event_types") or event_type).split("+") if e}
        is_book_move = event_type == "BOOK_MOVE"
        event_direction = str(signal.get("event_direction") or "")
        signal_match_id = str(game.get("match_id") or game.get("lobby_id") or "")

        # Block ALL second entries on a match.
        # 2026-05-29 added opposite-direction allowance, then REVERTED after
        # 14-trade backtest: 0/4 win rate on opposite POLL_FIGHT_SWING, -$28.77
        # net across all opposite trades. 8 matches went from winning first
        # bet to worse total with opposite. The engine's first-direction
        # signal is good; opposite-direction "reversal" signals are mostly
        # noise that dilute winning positions.
        existing = self._submitted_match_sides.get(signal_match_id)
        existing_dirs = (set(existing) if isinstance(existing, (list, set))
                         else ({existing} if existing else set()))
        if existing_dirs:
            reason = ("match_already_submitted" if event_direction in existing_dirs
                      else "match_direction_conflict")
            return self._reject(signal, mapping, game, reason)

        if not is_book_move:
            if LIVE_REQUIRE_CADENCE_SCHEMA and signal.get("event_schema_version") != "cadence_v1":
                return self._reject(signal, mapping, game, "missing_cadence_event_schema")
            cadence_quality = str(signal.get("source_cadence_quality") or "")
            if LIVE_ALLOWED_CADENCE_QUALITIES and cadence_quality and cadence_quality not in LIVE_ALLOWED_CADENCE_QUALITIES:
                return self._reject(
                    signal, mapping, game,
                    f"cadence_quality_not_live_allowed:got={cadence_quality}_allowed={','.join(sorted(LIVE_ALLOWED_CADENCE_QUALITIES))}",
                )
            event_quality = _to_float(signal.get("event_quality"))
            if event_quality is None or event_quality < LIVE_MIN_EVENT_QUALITY:
                _q = f"{event_quality:.3f}" if event_quality is not None else "None"
                return self._reject(
                    signal, mapping, game,
                    f"event_quality_too_low:q={_q}_min={LIVE_MIN_EVENT_QUALITY:.3f}",
                )
            if event_type == "POLL_DECISIVE_STOMP" and (event_quality is None or event_quality < LIVE_MIN_DECISIVE_STOMP_QUALITY):
                _q = f"{event_quality:.3f}" if event_quality is not None else "None"
                return self._reject(
                    signal, mapping, game,
                    f"decisive_stomp_quality_too_low:q={_q}_min={LIVE_MIN_DECISIVE_STOMP_QUALITY:.3f}",
                )
        if event_type == "POLL_DECISIVE_STOMP":
            ask = _to_float(signal.get("ask"))
            # Below 0.65 the two losses at 0.60/0.63 dominate; stomp signal is unreliable at low prices
            if ask is not None and ask < 0.65:
                return self._reject(signal, mapping, game, f"decisive_stomp_price_below_floor:ask={ask:.4f}_floor=0.6500")
        if event_type == "POLL_FIGHT_SWING":
            ask = _to_float(signal.get("ask"))
            # Above 0.82 win rate drops sharply; alpha concentrated at ask ≤ 0.82
            if ask is not None and ask > 0.82:
                return self._reject(signal, mapping, game, f"fight_swing_price_above_cap:ask={ask:.4f}_cap=0.8200")
        if event_type == "OBJECTIVE_CONVERSION_T3":
            ask = _to_float(signal.get("ask"))
            edge = _to_float(signal.get("executable_edge"))
            if ask is not None and ask > 0.85 and (edge is None or edge < 0.08):
                _e = f"{edge:.4f}" if edge is not None else "None"
                return self._reject(
                    signal, mapping, game,
                    f"objective_conversion_t3_requires_8c_edge_above_85c:ask={ask:.4f}_edge={_e}",
                )
        _ask_terminal = _to_float(signal.get("ask"))
        if _ask_terminal is not None and _ask_terminal >= 0.95 and event_type != "THRONE_EXPOSED":
            return self._reject(signal, mapping, game, f"chasing_terminal_price:ask={_ask_terminal:.4f}")
        if not is_book_move:
            if DISABLE_STRUCTURE_TRADES and (event_type in STRUCTURE_EVENTS or cluster_types <= STRUCTURE_EVENTS):
                return self._reject(signal, mapping, game, "structure_trade_disabled")
            if TRADE_EVENTS and not (event_type in TRADE_EVENTS or cluster_types & TRADE_EVENTS):
                return self._reject(signal, mapping, game, "event_not_allowed")
            # 2026-06-01 — Events explicitly in TRADE_EVENTS (operator allowlist)
            # override the taxonomy tier. POLL_FIRST_SWING_SETTLE (S1, the core
            # strategy) has tier "unknown" because it was never added to the
            # event_taxonomy tiers — so this gate rejected it as
            # "unknown_event_not_live_tradable" even though it's the primary
            # tradeable event. TRADE_EVENTS is the authoritative tradeable set.
            tier = event_tier(event_type)
            if event_type not in TRADE_EVENTS:
                if tier == "C" and not ALLOW_CONFIRMATION_ONLY_LIVE_TRADES:
                    return self._reject(signal, mapping, game, "confirmation_only_event")
                if tier in {"research", "block", "unknown"}:
                    return self._reject(signal, mapping, game, f"{tier}_event_not_live_tradable")

        fair = _to_float(signal.get("fair_price"))
        lag = _to_float(signal.get("lag"))
        edge = _to_float(signal.get("executable_edge"))
        # 2026-06-01 — Hold-to-settle events (EXIT_HORIZON=0) hold to $0/$1, so the
        # momentum gates (edge/lag = short-horizon markout) don't apply — the cap +
        # settle win-rate is the EV check. signal_engine already bypasses these for
        # hold-to-settle, but the live_executor had its OWN copies that still rejected
        # every S1/coverage signal (edge≈0 or negative when holding to settle). This
        # was blocking trades even after they passed signal_engine. Mirror the bypass.
        from config import EXIT_HORIZON_BY_EVENT as _EH_LX
        _is_hts_lx = _EH_LX.get(event_type, None) == 0
        if fair is None and not _is_hts_lx:
            return self._reject(signal, mapping, game, "missing_fair_price")
        if not _is_hts_lx:
            if edge is None or edge < MIN_EXECUTABLE_EDGE:
                _e = f"{edge:.4f}" if edge is not None else "None"
                return self._reject(signal, mapping, game, f"edge_too_small:edge={_e}_min={MIN_EXECUTABLE_EDGE:.4f}")
            if not is_book_move and (lag is None or lag < MIN_LAG):
                _l = f"{lag:.4f}" if lag is not None else "None"
                return self._reject(signal, mapping, game, f"lag_too_small:lag={_l}_min={MIN_LAG:.4f}")

        steam_age = age_ms(game.get("received_at_ns"))
        if not is_book_move:
            if steam_age > MAX_STEAM_AGE_MS:
                return self._reject(signal, mapping, game, f"steam_stale:age_ms={steam_age}_max={MAX_STEAM_AGE_MS}")

        token_id = str(signal.get("token_id") or "")
        book = book_store.get(token_id) if book_store else None
        if not book:
            return self._reject(signal, mapping, game, "missing_live_book")
        book_age = age_ms(book.get("received_at_ns"))
        if book_age > MAX_BOOK_AGE_MS:
            return self._reject(signal, mapping, game, f"book_stale:age_ms={book_age}_max={MAX_BOOK_AGE_MS}")
        ask = _to_float(book.get("best_ask"))
        bid = _to_float(book.get("best_bid"))
        if ask is None or bid is None:
            return self._reject(signal, mapping, game, "missing_bid_or_ask")
        if ask < 0.05:
            return self._reject(signal, mapping, game, f"market_near_zero:ask={ask:.4f}")
        spread = ask - bid
        if spread > MAX_SPREAD:
            return self._reject(signal, mapping, game, f"spread_too_wide:spread={spread:.4f}_max={MAX_SPREAD:.4f}")
        event_max_fill = _to_float(signal.get("max_fill_price")) or DEFAULT_MAX_FILL_PRICE
        event_max_fill = min(max(event_max_fill, 0.0), 0.99)
        if ask > event_max_fill:
            return self._reject(signal, mapping, game, f"ask_above_event_max_fill:ask={ask:.4f}_cap={event_max_fill:.4f}")
        if ask >= 0.95 and event_type != "THRONE_EXPOSED":
            return self._reject(signal, mapping, game, f"chasing_terminal_price:ask={ask:.4f}")

        # Recompute edge against the fresh best ask immediately before submission.
        # For BOOK_MOVE signals, skip fresh_edge check — the price_cap gate is the real safety net,
        # and fresh_edge failures occur when price moves 1-2 ticks during async scheduling.
        fresh_edge = (fair - ask) if fair is not None else 0.0
        if not is_book_move and not _is_hts_lx and fresh_edge < MIN_EXECUTABLE_EDGE:
            return self._reject(signal, mapping, game, f"fresh_edge_too_small:fresh_edge={fresh_edge:.4f}_min={MIN_EXECUTABLE_EDGE:.4f}")
        if event_type == "OBJECTIVE_CONVERSION_T3" and ask > 0.85 and fresh_edge < 0.08:
            return self._reject(signal, mapping, game, f"objective_conversion_t3_requires_8c_fresh_edge_above_85c:ask={ask:.4f}_fresh_edge={fresh_edge:.4f}")

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
            _fak_ticks = float(os.getenv("LIVE_FAK_BUFFER_TICKS", "4"))
            price_cap = round_down_to_tick(effective_ask + _fak_ticks * float(tick_size), tick_size)
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
            _enter_mid = os.getenv("LIVE_GTC_ENTER_AT_MID", "false").lower() in {"1","true","yes"}
            if _enter_mid and ask is not None and bid is not None:
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
            if cached_balance is not None and cached_balance + 1e-6 < order_usd:
                return self._reject(
                    signal, mapping, game,
                    f"insufficient_balance_cached:bal={cached_balance:.4f}_need={order_usd:.4f}",
                    price_cap=price_cap,
                )

        neg_risk = bool(mapping.get("neg_risk", False))
        attempt = LiveOrderAttempt(
            event_type=event_type,
            event_direction=str(signal.get("event_direction") or ""),
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
                resp = await self.client.buy_fak_market(
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

    async def try_buy_continuous(
        self,
        *,
        signal: Any,            # continuous_scorer.ContinuousSignal
        mapping: dict,
        game: dict,
        book_store,
    ) -> LiveOrderAttempt:
        """Submit a FAK buy from a ContinuousSignal.

        Deliberately bypasses the event-detector concepts (event_type,
        cluster_event_types, event_quality, etc.). The continuous strategy
        carries its own gates upstream; here we just check budget,
        compute price_cap, and submit.

        Position metadata `exit_horizon_sec` flows through to the exit
        engine via the persisted live_position record.
        """
        signal_match_id = str(game.get("match_id") or game.get("lobby_id") or "")
        # Pick the YES or NO token depending on which side the signal favors.
        token_id = mapping.get("yes_token_id") if signal.side == "YES" else mapping.get("no_token_id")
        if not token_id:
            return LiveOrderAttempt(
                event_type="CONTINUOUS",
                event_direction="radiant" if signal.direction > 0 else "dire",
                token_id="",
                side=signal.side,
                fair_price=signal.ref_mid_blended,
                best_ask=None, price_cap=None, edge=None, lag=None, spread=None,
                book_age_ms=None, steam_age_ms=None,
                order_type="FAK",
                submitted_size_usd=0.0,
                order_status="rejected_precheck",
                reason_if_rejected="missing_token_id",
                match_id=signal_match_id,
                game_time_sec=game.get("game_time_sec"),
                created_at_ns=time.time_ns(),
                trader_kind="continuous",
                exit_horizon_sec=signal.exit_horizon_sec,
                signal_id=signal.signal_id,
            )

        # --- budget gates ---
        value_live_max_usd = float(os.getenv("VALUE_LIVE_MAX_USD", "20.0"))
        size_usd = min(float(signal.sized_usd), MAX_TRADE_USD, value_live_max_usd)
        if not ALLOW_EVENT_TRADES:
            return self._reject_continuous(signal, mapping, game, token_id, size_usd, "event_trades_disabled")
        if self.total_submitted_usd + size_usd > MAX_TOTAL_LIVE_USD:
            return self._reject_continuous(signal, mapping, game, token_id, size_usd, "max_total_live_usd_reached")
        if self.open_positions >= MAX_OPEN_POSITIONS:
            return self._reject_continuous(signal, mapping, game, token_id, size_usd, "max_open_positions_reached")
        if self.daily_realized_pnl_usd <= -MAX_DAILY_DRAWDOWN_USD:
            return self._reject_continuous(
                signal, mapping, game, token_id, size_usd,
                f"daily_drawdown_circuit_breaker:{self.daily_realized_pnl_usd:.2f}",
            )
        disk_reason = self.disk_guard.reject_reason()
        if disk_reason:
            return self._reject_continuous(signal, mapping, game, token_id, size_usd, disk_reason)

        # --- book lookup ---
        book = book_store.get(token_id) if book_store is not None else None
        ask = _to_float(book.get("best_ask")) if book else None
        bid = _to_float(book.get("best_bid")) if book else None
        if ask is None:
            return self._reject_continuous(signal, mapping, game, token_id, size_usd, "missing_ask")

        # Spread gate: if (ask - bid) > CONTINUOUS_MAX_SPREAD, the position is
        # condemned at entry — adverse_stop fires at entry-4c. Reject before submit.
        if bid is not None and (ask - bid) > CONTINUOUS_MAX_SPREAD:
            return self._reject_continuous(signal, mapping, game, token_id, size_usd,
                f"spread_too_wide:spread={ask-bid:.3f}_max={CONTINUOUS_MAX_SPREAD:.3f}")

        # Re-entry cooldown: block the same (match, direction) for N seconds.
        # Prevents the engine from immediately re-entering after an adverse_stop.
        if CONTINUOUS_REENTRY_COOLDOWN_SEC > 0:
            cd_key = f"{signal_match_id}|{signal.direction}"
            last_entry_ns = self._continuous_last_entry_ns.get(cd_key, 0)
            age = (time.time_ns() - last_entry_ns) / 1e9
            if last_entry_ns > 0 and age < CONTINUOUS_REENTRY_COOLDOWN_SEC:
                return self._reject_continuous(signal, mapping, game, token_id, size_usd,
                    f"continuous_cooldown:age={age:.1f}s_min={CONTINUOUS_REENTRY_COOLDOWN_SEC:.0f}s")

        tick_size = str(mapping.get("tick_size") or LIVE_TICK_SIZE)
        try:
            _tick = float(tick_size)
        except ValueError:
            _tick = 0.01
        # FAK price cap: 2 ticks above current ask. Accepts mild slippage.
        price_cap = round(ask + 2 * _tick, 4)
        neg_risk = bool(mapping.get("neg_risk", False))

        spread = (ask - bid) if (ask is not None and bid is not None) else None

        attempt = LiveOrderAttempt(
            event_type="CONTINUOUS",
            event_direction="radiant" if signal.direction > 0 else "dire",
            token_id=str(token_id),
            side=signal.side,
            fair_price=signal.ref_mid_blended,
            best_ask=ask,
            price_cap=price_cap,
            edge=None,                          # continuous doesn't define a single edge
            lag=None,
            spread=round(spread, 4) if spread is not None else None,
            book_age_ms=None,
            steam_age_ms=None,
            order_type="FAK",
            submitted_size_usd=size_usd,
            market_name=mapping.get("name"),
            match_id=signal_match_id,
            game_time_sec=signal.game_time_sec,
            created_at_ns=time.time_ns(),
            trader_kind="continuous",
            exit_horizon_sec=signal.exit_horizon_sec,
            signal_id=signal.signal_id,
        )

        attempt.submit_start_ns = time.time_ns()
        if not ENABLE_REAL_LIVE_TRADING:
            attempt.response_received_ns = time.time_ns()
            attempt.order_status = "filled"
            attempt.reason_if_rejected = "paper_simulated"
            attempt.order_id = f"paper_entry_{time.time_ns()}"
            attempt.filled_size_usd = round(size_usd, 6)
            attempt.avg_fill_price = attempt.best_ask
            self.total_submitted_usd += size_usd
            self.total_filled_usd += attempt.filled_size_usd
            self.open_positions += 1
            self._continuous_last_entry_ns[f"{signal_match_id}|{signal.direction}"] = time.time_ns()
            self._save()
            return attempt

        if self.client is None:
            self.client = LiveCLOBClient()

        try:
            resp = await self.client.buy_fak_market(
                token_id=str(token_id),
                amount_usd=size_usd,
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
        # 2026-06-02 — fill-price capture fix. A FAK that lands in the sequencer
        # comes back "delayed"/"live" with filled_size=0, so _avg_fill_price
        # returns None and the entry price was lost (logged empty) even though the
        # order WILL fill at <= price_cap. Record price_cap as the entry price for
        # any working/filled FAK so live P&L and the shadow validation have a real
        # number instead of a blank. (FAK can only fill at or below price_cap.)
        if attempt.avg_fill_price is None and attempt.order_status in ("delayed", "live", "filled", "matched"):
            attempt.avg_fill_price = price_cap

        # Update budget on a successful submission. FAK is fill-or-kill so
        # any non-zero filled_size_usd or pending/live state consumes budget.
        if attempt.filled_size_usd > 0 or attempt.order_status in ("delayed", "live"):
            self.total_submitted_usd += size_usd
            self.total_filled_usd += attempt.filled_size_usd
            self.open_positions += 1
            self._continuous_last_entry_ns[f"{signal_match_id}|{signal.direction}"] = time.time_ns()
            self._save()

        return attempt

    def _reject_value(self, signal, mapping, game, token_id, size_usd, reason) -> LiveOrderAttempt:
        return LiveOrderAttempt(
            event_type="VALUE",
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
            trader_kind="value",
            exit_horizon_sec=None,
            signal_id=signal.signal_id,
        )

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

        # --- budget gates ---
        size_usd = min(float(signal.sized_usd), MAX_TRADE_USD)
        if not ALLOW_EVENT_TRADES:
            return self._reject_value(signal, mapping, game, token_id, size_usd, "event_trades_disabled")
        if self.total_submitted_usd + size_usd > MAX_TOTAL_LIVE_USD:
            return self._reject_value(signal, mapping, game, token_id, size_usd, "max_total_live_usd_reached")
        if self.open_positions >= MAX_OPEN_POSITIONS:
            return self._reject_value(signal, mapping, game, token_id, size_usd, "max_open_positions_reached")
        if self.daily_realized_pnl_usd <= -MAX_DAILY_DRAWDOWN_USD:
            return self._reject_value(
                signal, mapping, game, token_id, size_usd,
                f"daily_drawdown_circuit_breaker:{self.daily_realized_pnl_usd:.2f}")
        disk_reason = self.disk_guard.reject_reason()
        if disk_reason:
            return self._reject_value(signal, mapping, game, token_id, size_usd, disk_reason)
        # Per-match exposure cap — the HARD backstop against over-stacking. A value
        # bet is hold-to-settle and wants ~ONE entry per match; this caps cumulative
        # submitted USD per match at VALUE_MAX_PER_MATCH (≈ one trade) regardless of
        # whether the dedup/recording fires. This is what dumped ~$50 into Inner
        # Circle/MODUS (no cap on this path). Persisted in _submitted_match_usd.
        _vmpm = float(os.getenv("VALUE_MAX_PER_MATCH", "6.0"))
        match_used = self._submitted_match_usd.get(signal_match_id, 0.0)
        if match_used + size_usd > _vmpm:
            return self._reject_value(signal, mapping, game, token_id, size_usd,
                                      f"value_match_cap:used={match_used:.1f}_cap={_vmpm:.1f}")

        # Re-entry cooldown per (match, direction). Hold-to-settle wants ONE bet
        # per match-side; the cooldown plus the caller's open-position check guard
        # against re-buying every poll.
        cd_key = f"value|{signal_match_id}|{signal.direction}"
        _cool = max(CONTINUOUS_REENTRY_COOLDOWN_SEC, 60)
        last_ns = self._continuous_last_entry_ns.get(cd_key, 0)
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
        _fak_ticks = float(os.getenv("VALUE_FAK_BUFFER_TICKS", "2"))
        price_cap = round(min(ask + _fak_ticks * _tick, signal.fair_price), 4)
        neg_risk = bool(mapping.get("neg_risk", False))

        attempt = LiveOrderAttempt(
            event_type="VALUE",
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
            order_type="FAK",
            submitted_size_usd=size_usd,
            market_name=mapping.get("name"),
            match_id=signal_match_id,
            game_time_sec=signal.game_time_sec,
            created_at_ns=time.time_ns(),
            trader_kind="value",
            exit_horizon_sec=None,
            signal_id=signal.signal_id,
        )

        attempt.submit_start_ns = time.time_ns()
        if not ENABLE_REAL_LIVE_TRADING:
            attempt.response_received_ns = time.time_ns()
            attempt.order_status = "filled"
            attempt.reason_if_rejected = "paper_simulated"
            attempt.order_id = f"paper_value_{time.time_ns()}"
            attempt.filled_size_usd = round(size_usd, 6)
            attempt.avg_fill_price = attempt.best_ask
            self.total_submitted_usd += size_usd
            self.total_filled_usd += attempt.filled_size_usd
            self.open_positions += 1
            self._submitted_match_usd[signal_match_id] = self._submitted_match_usd.get(signal_match_id, 0.0) + size_usd
            self._continuous_last_entry_ns[cd_key] = time.time_ns()
            self._save()
            return attempt

        if self.client is None:
            self.client = LiveCLOBClient()

        try:
            resp = await self.client.buy_fak_market(
                token_id=str(token_id),
                amount_usd=size_usd,
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
            self._continuous_last_entry_ns[cd_key] = time.time_ns()
            self._save()

        # Marketable GTC rests after Polymarket's ~1s delay — poll for fill; cancel + release at 30s.
        if attempt.order_status in ("delayed", "live") and attempt.order_id:
            asyncio.ensure_future(
                self._poll_and_cancel_delayed(
                    order_id=attempt.order_id, order_usd=size_usd,
                    match_id=signal_match_id, attempt=attempt,
                )
            )

        return attempt

    async def try_buy_arb(
        self,
        *,
        opportunity: Any,        # arb_scanner.ArbOpportunity
        mapping: dict,
        game: dict,
    ) -> tuple[LiveOrderAttempt, LiveOrderAttempt]:
        """Submit both YES and NO FAK legs for one ArbOpportunity.

        Returns (yes_attempt, no_attempt). Either or both may be rejected;
        when only one leg fills, the caller is responsible for force-exit
        of the unmatched leg via the existing exit machinery.
        """
        tick_size = str(mapping.get("tick_size") or LIVE_TICK_SIZE)
        try:
            _tick = float(tick_size)
        except ValueError:
            _tick = 0.01
        neg_risk = bool(mapping.get("neg_risk", False))

        # 2026-05-29 Phase AR-3 — matched-shares split. yes_usd and no_usd are
        # different dollar amounts that buy IDENTICAL share counts at each
        # side's ask. Guarantees $1 payout per share at settle regardless of
        # which side wins.
        yes_attempt = await self._submit_arb_leg(
            token_id=opportunity.yes_token_id,
            side="YES",
            ask=opportunity.yes_ask,
            leg_size_usd=opportunity.yes_usd,
            tick_size=tick_size, neg_risk=neg_risk,
            mapping=mapping, game=game, arb_id=opportunity.arb_id,
        )
        no_attempt = await self._submit_arb_leg(
            token_id=opportunity.no_token_id,
            side="NO",
            ask=opportunity.no_ask,
            leg_size_usd=opportunity.no_usd,
            tick_size=tick_size, neg_risk=neg_risk,
            mapping=mapping, game=game, arb_id=opportunity.arb_id,
        )
        return yes_attempt, no_attempt

    async def _submit_arb_leg(
        self, *, token_id: str, side: str, ask: float, leg_size_usd: float,
        tick_size: str, neg_risk: bool,
        mapping: dict, game: dict, arb_id: str,
    ) -> LiveOrderAttempt:
        # Pre-trade budget check applies per-leg.
        if self.total_submitted_usd + leg_size_usd > MAX_TOTAL_LIVE_USD:
            return self._reject_arb_leg(
                token_id=token_id, side=side, ask=ask,
                leg_size_usd=leg_size_usd, mapping=mapping, game=game,
                arb_id=arb_id, reason="max_total_live_usd_reached",
            )
        if self.open_positions >= MAX_OPEN_POSITIONS:
            return self._reject_arb_leg(
                token_id=token_id, side=side, ask=ask,
                leg_size_usd=leg_size_usd, mapping=mapping, game=game,
                arb_id=arb_id, reason="max_open_positions_reached",
            )

        try:
            _tick = float(tick_size)
        except ValueError:
            _tick = 0.01
        price_cap = round(ask + 2 * _tick, 4)

        attempt = LiveOrderAttempt(
            event_type="ARB",
            event_direction="",
            token_id=token_id,
            side=side,
            fair_price=None,
            best_ask=ask,
            price_cap=price_cap,
            edge=None, lag=None, spread=None,
            book_age_ms=None, steam_age_ms=None,
            order_type="FAK",
            submitted_size_usd=leg_size_usd,
            market_name=mapping.get("name"),
            match_id=str(game.get("match_id") or game.get("lobby_id") or ""),
            game_time_sec=game.get("game_time_sec"),
            created_at_ns=time.time_ns(),
            trader_kind="arb",
            exit_horizon_sec=None,    # hold to settlement
            signal_id=arb_id,
        )

        attempt.submit_start_ns = time.time_ns()
        if not ENABLE_REAL_LIVE_TRADING:
            attempt.response_received_ns = time.time_ns()
            attempt.order_status = "filled"
            attempt.reason_if_rejected = "paper_simulated"
            attempt.order_id = f"paper_arb_{time.time_ns()}"
            attempt.filled_size_usd = round(leg_size_usd, 6)
            attempt.avg_fill_price = attempt.best_ask
            self.total_submitted_usd += leg_size_usd
            self.total_filled_usd += attempt.filled_size_usd
            self.open_positions += 1
            self._save()
            return attempt

        if self.client is None:
            self.client = LiveCLOBClient()

        try:
            resp = await self.client.buy_fak_market(
                token_id=token_id, amount_usd=leg_size_usd,
                price_cap=price_cap, tick_size=tick_size, neg_risk=neg_risk,
            )
            attempt.response_received_ns = time.time_ns()
        except Exception as exc:
            attempt.response_received_ns = time.time_ns()
            attempt.order_status = "exception"
            attempt.reason_if_rejected = repr(exc)
            return attempt

        if attempt.submit_start_ns and attempt.response_received_ns:
            attempt.submit_latency_ms = round(
                (attempt.response_received_ns - attempt.submit_start_ns) / 1_000_000, 2)

        attempt.raw_response_json = json.dumps(_jsonable(resp), sort_keys=True)[:4000]
        attempt.order_status = _status_from_response(resp)
        attempt.reason_if_rejected = _error_from_response(resp)
        attempt.order_id = _order_id_from_response(resp)
        attempt.filled_size_usd = round(_filled_usd_from_response(resp, leg_size_usd), 6)
        attempt.avg_fill_price = _avg_fill_price(resp, price_cap, attempt.filled_size_usd)

        if attempt.filled_size_usd > 0 or attempt.order_status in ("delayed", "live"):
            self.total_submitted_usd += leg_size_usd
            self.total_filled_usd += attempt.filled_size_usd
            self.open_positions += 1
            self._save()
        return attempt

    def _reject_arb_leg(self, *, token_id, side, ask, leg_size_usd, mapping, game, arb_id, reason) -> LiveOrderAttempt:
        return LiveOrderAttempt(
            event_type="ARB",
            event_direction="",
            token_id=str(token_id or ""),
            side=side,
            fair_price=None,
            best_ask=ask,
            price_cap=None,
            edge=None, lag=None, spread=None,
            book_age_ms=None, steam_age_ms=None,
            order_type="FAK",
            submitted_size_usd=0.0,
            order_status="rejected_precheck",
            reason_if_rejected=reason,
            market_name=mapping.get("name"),
            match_id=str(game.get("match_id") or game.get("lobby_id") or ""),
            game_time_sec=game.get("game_time_sec"),
            created_at_ns=time.time_ns(),
            trader_kind="arb",
            exit_horizon_sec=None,
            signal_id=arb_id,
        )

    def _reject_continuous(self, signal, mapping, game, token_id, size_usd, reason) -> LiveOrderAttempt:
        return LiveOrderAttempt(
            event_type="CONTINUOUS",
            event_direction="radiant" if signal.direction > 0 else "dire",
            token_id=str(token_id or ""),
            side=signal.side,
            fair_price=signal.ref_mid_blended,
            best_ask=None, price_cap=None, edge=None, lag=None, spread=None,
            book_age_ms=None, steam_age_ms=None,
            order_type="FAK",
            submitted_size_usd=0.0,
            order_status="rejected_precheck",
            reason_if_rejected=reason,
            market_name=mapping.get("name"),
            match_id=str(game.get("match_id") or game.get("lobby_id") or ""),
            game_time_sec=signal.game_time_sec,
            created_at_ns=time.time_ns(),
            trader_kind="continuous",
            exit_horizon_sec=signal.exit_horizon_sec,
            signal_id=signal.signal_id,
        )

    async def _poll_and_cancel_delayed(
        self,
        *,
        order_id: str,
        order_usd: float,
        match_id: str,
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
                self.release_submitted_budget(order_usd, match_id=match_id)
                self.decrement_open_positions(match_id)
                await self._emit_delayed_resolution(attempt)
                return

        # Still pending after 30s — cancel and release
        logger.warning("[delayed_poll] order=%s still pending at 30s — cancelling", order_id)
        try:
            if self.client is None:
                self.client = LiveCLOBClient()
            await self.client.cancel_order_by_id(order_id)
        except Exception as exc:
            logger.warning("[delayed_poll] cancel order=%s failed: %s", order_id, exc)
        if attempt is not None:
            attempt.order_status = "cancelled"
            attempt.reason_if_rejected = "delayed_order_timeout_cancelled"
        self.release_submitted_budget(order_usd, match_id=match_id)
        self.decrement_open_positions(match_id)
        await self._emit_delayed_resolution(attempt)

    async def _poll_order_status(self, order_id: str) -> dict:
        if self.client is None:
            self.client = LiveCLOBClient()
        try:
            resp = await self.client.get_order_status(order_id)
            return _response_to_dict(resp) if isinstance(resp, dict) else {"raw": str(resp)}
        except Exception as exc:
            return {"error": repr(exc)}
