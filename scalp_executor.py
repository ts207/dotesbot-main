"""Buy-both-scalp executor — pre-game scalping strategy.

Strategy (backtest: +$0.10/pair filtered, 73% win, max DD 1%, n=11):
  1. On market mapping: when both YES and NO are quotable, check the FILTER:
        |yes_ask - no_ask| <= SCALP_MAX_SKEW       (0.08 = tight match)
        yes_ask + no_ask <= SCALP_MAX_SUM          (1.03 = no excessive premium)
  2. If qualifies: place GTC limit BUYs at both yes_ask and no_ask
  3. Once both fill (using ACTUAL filled shares, not estimated): place GTC limit
     SELLs at entry+SCRATCH_CENTS on each side
  4. First side to scratch closes out (recovers ~$0.52)
  5. Other side rides to either:
       - bid >= SCALP_RIDE_TARGET (sells at peak), or
       - settlement (game over → close at bid)

Reliability features:
  - Tracks ACTUAL filled shares from each buy (avoids sell-overshare rejects)
  - Retries failed orders up to SCALP_MAX_RETRIES with refreshed price
  - Tracks realized P&L per pair, logged to logs/scalp_trades.csv on close
  - Force-closes legs at bid on game_over (safety net for un-scratched legs)

Sizing: SCALP_STAKE_USD per leg ($stake on YES + $stake on NO = 2x notional).
"""
from __future__ import annotations

import asyncio
import csv
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import LIVE_TRADING, ENABLE_REAL_LIVE_TRADING

LOG = logging.getLogger(__name__)

# ---------- Tunables (overridable via env) ----------
SCALP_ENABLED = os.getenv("SCALP_ENABLED", "false").lower() in {"1", "true", "yes"}
SCALP_STAKE_USD = float(os.getenv("SCALP_STAKE_USD", "10"))
SCALP_MAX_SKEW = float(os.getenv("SCALP_MAX_SKEW", "0.08"))
SCALP_MAX_SUM = float(os.getenv("SCALP_MAX_SUM", "1.03"))
SCALP_MIN_PRICE = float(os.getenv("SCALP_MIN_PRICE", "0.40"))
SCALP_MAX_PRICE = float(os.getenv("SCALP_MAX_PRICE", "0.60"))
SCALP_SCRATCH_CENTS = float(os.getenv("SCALP_SCRATCH_CENTS", "0.02"))
SCALP_RIDE_TARGET = float(os.getenv("SCALP_RIDE_TARGET", "0.90"))
SCALP_MAX_OPEN_PAIRS = int(os.getenv("SCALP_MAX_OPEN_PAIRS", "3"))
SCALP_MAX_RETRIES = int(os.getenv("SCALP_MAX_RETRIES", "2"))
SCALP_TRADES_CSV_PATH = os.getenv("SCALP_TRADES_CSV_PATH", "logs/scalp_trades.csv")

# 2026-05-27 improvements — match the LoL scalp logic
SCALP_MIN_BID_SIZE_USD = float(os.getenv("SCALP_MIN_BID_SIZE_USD", "100"))
SCALP_MAX_BOOK_SPREAD = float(os.getenv("SCALP_MAX_BOOK_SPREAD", "0.04"))
SCALP_STOP_LOSS_CENTS = float(os.getenv("SCALP_STOP_LOSS_CENTS", "0.25"))
SCALP_RIDE_TRAIL_CENTS = float(os.getenv("SCALP_RIDE_TRAIL_CENTS", "0.10"))
SCALP_RIDE_TRAIL_MIN_PEAK = float(os.getenv("SCALP_RIDE_TRAIL_MIN_PEAK", "0.60"))
SCALP_MAX_HOLD_MIN = float(os.getenv("SCALP_MAX_HOLD_MIN", "90"))
SCALP_MAX_PAIRS_PER_SERIES = int(os.getenv("SCALP_MAX_PAIRS_PER_SERIES", "1"))
SCALP_COOLDOWN_AFTER_LOSS_SEC = float(os.getenv("SCALP_COOLDOWN_AFTER_LOSS_SEC", "300"))

# 2026-05-27 tiered in-game scalp — extend window past pre-game
# Tier 1 PRE-MATCH    (gt is None or 0): standard filter
# Tier 2 EARLY GAME   (0 < gt < EARLY_GAME_SEC): standard filter
# Tier 3 MID GAME     (EARLY_GAME_SEC <= gt < MAX_GAME_SEC): stricter filter
# Tier 4 LATE GAME    (gt >= MAX_GAME_SEC): SKIP
SCALP_EARLY_GAME_SEC = int(os.getenv("SCALP_EARLY_GAME_SEC", "600"))      # 10 min
SCALP_MAX_GAME_TIME_SEC = int(os.getenv("SCALP_MAX_GAME_TIME_SEC", "1800"))  # 30 min
# Stricter gates for mid-game (Tier 3)
SCALP_MID_GAME_MAX_SUM = float(os.getenv("SCALP_MID_GAME_MAX_SUM", "1.00"))
SCALP_MID_GAME_MAX_SKEW = float(os.getenv("SCALP_MID_GAME_MAX_SKEW", "0.05"))
SCALP_MID_GAME_MIN_BID_USD = float(os.getenv("SCALP_MID_GAME_MIN_BID_USD", "200"))
SCALP_MID_GAME_STOP_LOSS_CENTS = float(os.getenv("SCALP_MID_GAME_STOP_LOSS_CENTS", "0.15"))

# 2026-05-29 Phase SC-1 — cross-book disagreement gate.
# Scalp wants the two books to agree on fair value. When |YES_mid - (1 - NO_mid)| > 2c
# the market is in a transient repricing state and scalp should sit it out.
# Derived from dual-sided data study: 4.9% of synced pairs had > 2c disagreement,
# median 3c, max 34c. Those moments are noise; scratch+ride PnL suffers.
SCALP_CROSS_BOOK_DISAGREE_MAX = float(os.getenv("SCALP_CROSS_BOOK_DISAGREE_MAX", "0.02"))

# Polymarket-ish maker fee — used for P&L estimation only (actual fees come from
# the CLOB response when shipped). 2% per fill on each side ≈ 4% round trip.
_FEE_RATE = 0.02


@dataclass
class ScalpLeg:
    """One side (YES or NO) of a scalp pair."""
    token: str
    entry_px: float
    intended_shares: float        # what we asked for
    buy_order_id: str | None = None
    buy_attempts: int = 0
    buy_filled: bool = False
    filled_shares: float = 0.0    # ACTUAL shares received
    filled_avg_px: float = 0.0    # ACTUAL avg fill price
    scratch_order_id: str | None = None
    scratch_attempts: int = 0
    scratch_filled: bool = False
    scratch_filled_px: float = 0.0
    closed: bool = False          # ride closed or scratch filled
    realized_pnl: float = 0.0     # contribution to pair P&L


@dataclass
class ScalpPair:
    market_id: str
    match_id: str
    yes: ScalpLeg
    no: ScalpLeg
    ride_token: str | None = None
    ride_peak_bid: float = 0.0
    opened_at_ns: int = field(default_factory=time.time_ns)
    closed_at_ns: int | None = None
    closed: bool = False
    close_reason: str = ""

    @property
    def realized_pnl_usd(self) -> float:
        return self.yes.realized_pnl + self.no.realized_pnl


def _parse_order_resp(resp: dict | None) -> tuple[str | None, float, float, str]:
    """Extract (order_id, filled_shares, avg_price, status) from CLOB response."""
    if not resp:
        return None, 0.0, 0.0, ""
    oid = resp.get("orderID") or resp.get("order_id") or resp.get("id")
    fs = resp.get("filledShares") or resp.get("filled_shares") or resp.get("sizeMatched") or 0
    avg = resp.get("avgFillPrice") or resp.get("avg_fill_price") or resp.get("averagePrice") or 0
    status = str(resp.get("status") or resp.get("orderStatus") or "").lower()
    try: fs = float(fs)
    except: fs = 0.0
    try: avg = float(avg)
    except: avg = 0.0
    return (str(oid) if oid else None), fs, avg, status


import re as _re


def _series_key(market_name: str) -> str:
    """Derive series key from market name so multi-game series share it."""
    q = (market_name or "").lower()
    q = _re.sub(r"\s*-\s*game\s*\d+\s*winner\s*$", "", q)
    q = _re.sub(r"\s*\(bo\d+\)\s*-\s*.*$", "", q)
    return q.strip()


class ScalpExecutor:
    """Manages buy-both-scalp positions."""

    def __init__(self, *, clob_client: Any = None):
        self._client = clob_client
        self._pairs: dict[str, ScalpPair] = {}
        self._considered: set[str] = set()
        self._series_open_count: dict[str, int] = {}
        self._cooldown_until_ns: int = 0

    def open_pairs(self) -> int:
        return sum(1 for p in self._pairs.values() if not p.closed)

    def total_realized_pnl(self) -> float:
        return sum(p.realized_pnl_usd for p in self._pairs.values())

    @staticmethod
    def qualifies(yes_ask: float | None, no_ask: float | None, *,
                  game_started: bool = False,
                  yes_book: dict | None = None,
                  no_book: dict | None = None,
                  game_time_sec: float | None = None) -> tuple[bool, str]:
        # Tier 4: too late — never enter past the max-game-time threshold
        if game_time_sec is not None and game_time_sec >= SCALP_MAX_GAME_TIME_SEC:
            return False, f"scalp_too_late_gt={int(game_time_sec)}"
        # Back-compat: respect game_started flag if caller set it explicitly
        if game_started and (game_time_sec is None or game_time_sec >= SCALP_MAX_GAME_TIME_SEC):
            return False, "scalp_post_kickoff"
        if yes_ask is None or no_ask is None: return False, "missing_ask"
        if not (SCALP_MIN_PRICE <= yes_ask <= SCALP_MAX_PRICE):
            return False, f"yes_price_out_of_range:{yes_ask:.3f}"
        if not (SCALP_MIN_PRICE <= no_ask <= SCALP_MAX_PRICE):
            return False, f"no_price_out_of_range:{no_ask:.3f}"

        # Tiered filter — stricter as game progresses
        in_mid_game = game_time_sec is not None and game_time_sec >= SCALP_EARLY_GAME_SEC
        max_skew = SCALP_MID_GAME_MAX_SKEW if in_mid_game else SCALP_MAX_SKEW
        max_sum = SCALP_MID_GAME_MAX_SUM if in_mid_game else SCALP_MAX_SUM
        min_bid_usd = SCALP_MID_GAME_MIN_BID_USD if in_mid_game else SCALP_MIN_BID_SIZE_USD

        skew = abs(yes_ask - no_ask)
        if skew > max_skew:
            return False, f"skew_{skew:.3f}_over_{max_skew:.2f}{'_midgame' if in_mid_game else ''}"
        s_sum = yes_ask + no_ask
        if s_sum > max_sum:
            return False, f"sum_{s_sum:.3f}_over_{max_sum:.2f}{'_midgame' if in_mid_game else ''}"

        # 2026-05-29 Phase SC-1 — cross-book disagreement.
        # Compute mid for each side from (bid + ask) / 2 when possible.
        # If either book is missing bid we use ask as a proxy; conservative
        # because falling back to ask widens the disagreement metric.
        if yes_book is not None and no_book is not None:
            y_bid = yes_book.get("best_bid")
            n_bid = no_book.get("best_bid")
            y_mid = (y_bid + yes_ask) / 2 if y_bid is not None else yes_ask
            n_mid = (n_bid + no_ask) / 2 if n_bid is not None else no_ask
            disagreement = abs(y_mid - (1.0 - n_mid))
            if disagreement > SCALP_CROSS_BOOK_DISAGREE_MAX:
                return False, f"cross_book_disagreement_{disagreement:.3f}"
        # Depth + spread gates
        for label, book in (("yes", yes_book), ("no", no_book)):
            if book is None: continue
            bid = book.get("best_bid"); ask = book.get("best_ask")
            bid_size = book.get("bid_size") or 0
            if bid is not None and ask is not None and (ask - bid) > SCALP_MAX_BOOK_SPREAD:
                return False, f"{label}_spread_{(ask-bid):.3f}_too_wide"
            if bid is not None and bid_size and (bid * bid_size) < min_bid_usd:
                return False, f"{label}_bid_${bid*bid_size:.0f}_below_{min_bid_usd:.0f}"
        return True, ""

    async def evaluate_market(self, *, market_id: str, match_id: str,
                              yes_token: str, no_token: str,
                              yes_ask: float | None, no_ask: float | None,
                              tick_size: str, neg_risk: bool,
                              game_started: bool,
                              yes_book: dict | None = None,
                              no_book: dict | None = None,
                              market_name: str = "",
                              game_time_sec: float | None = None) -> dict[str, Any]:
        if not SCALP_ENABLED: return {"action": "skip", "reason": "scalp_disabled"}
        if market_id in self._pairs: return {"action": "skip", "reason": "scalp_pair_already_open"}
        if market_id in self._considered: return {"action": "skip", "reason": "scalp_already_considered"}
        if self.open_pairs() >= SCALP_MAX_OPEN_PAIRS:
            return {"action": "skip", "reason": "scalp_max_pairs_reached"}

        # Global cooldown after loss
        if time.time_ns() < self._cooldown_until_ns:
            return {"action": "skip", "reason": "scalp_cooldown_after_loss"}

        # Per-series cap
        series = _series_key(market_name)
        if series and self._series_open_count.get(series, 0) >= SCALP_MAX_PAIRS_PER_SERIES:
            return {"action": "skip", "reason": f"scalp_series_cap:{series[:30]}"}

        ok, why = self.qualifies(yes_ask, no_ask, game_started=game_started,
                                  yes_book=yes_book, no_book=no_book,
                                  game_time_sec=game_time_sec)
        if not ok: return {"action": "skip", "reason": f"scalp_filter:{why}"}

        self._considered.add(market_id)

        pair = ScalpPair(
            market_id=market_id, match_id=match_id,
            yes=ScalpLeg(token=yes_token, entry_px=float(yes_ask),
                         intended_shares=round(SCALP_STAKE_USD / yes_ask, 4)),
            no=ScalpLeg(token=no_token, entry_px=float(no_ask),
                        intended_shares=round(SCALP_STAKE_USD / no_ask, 4)),
        )
        # Stash series key on the pair so we can decrement on close
        pair._series_key = series  # type: ignore[attr-defined]

        if not ENABLE_REAL_LIVE_TRADING:
            # 2026-05-27 — PAPER MODE: track the pair through full lifecycle.
            # Assume both buys fill instantly at ask. on_book_tick handles the
            # scratch/ride/stop/timeout state machine based on observed bids.
            pair.yes.buy_filled = True
            pair.yes.filled_shares = round(SCALP_STAKE_USD / yes_ask, 4)
            pair.yes.filled_avg_px = float(yes_ask)
            pair.no.buy_filled = True
            pair.no.filled_shares = round(SCALP_STAKE_USD / no_ask, 4)
            pair.no.filled_avg_px = float(no_ask)
            self._pairs[market_id] = pair
            if series:
                self._series_open_count[series] = self._series_open_count.get(series, 0) + 1
            LOG.info("SCALP paper-pair OPEN %s yes=%.3f no=%.3f", market_id, yes_ask, no_ask)
            return {"action": "scalp_paper_open", "market_id": market_id,
                    "yes_entry": yes_ask, "no_entry": no_ask}

        if self._client is None: return {"action": "skip", "reason": "scalp_no_client"}

        await self._place_buy(pair, leg=pair.yes, current_ask=yes_ask, tick_size=tick_size, neg_risk=neg_risk)
        await self._place_buy(pair, leg=pair.no, current_ask=no_ask, tick_size=tick_size, neg_risk=neg_risk)
        self._pairs[market_id] = pair
        LOG.info("SCALP opened pair %s yes=%.3f no=%.3f", market_id, yes_ask, no_ask)
        return {"action": "scalp_opened_pair", "market_id": market_id,
                "yes_entry": yes_ask, "no_entry": no_ask,
                "yes_order_id": pair.yes.buy_order_id, "no_order_id": pair.no.buy_order_id}

    async def _place_buy(self, pair: ScalpPair, *, leg: ScalpLeg, current_ask: float,
                          tick_size: str, neg_risk: bool) -> None:
        """Place a buy. Updates leg state. Caller handles retries via on_book_tick."""
        leg.buy_attempts += 1
        try:
            resp = await self._client.buy_gtc_limit(
                token_id=leg.token, amount_usd=SCALP_STAKE_USD,
                price_cap=current_ask, tick_size=tick_size, neg_risk=neg_risk,
                limit_price=current_ask,
            )
        except Exception as exc:
            LOG.warning("SCALP buy attempt %d failed %s: %s", leg.buy_attempts, leg.token, exc)
            return
        oid, fs, avg_px, status = _parse_order_resp(resp)
        leg.buy_order_id = oid
        if fs > 0:
            leg.filled_shares = fs
            leg.filled_avg_px = avg_px if avg_px > 0 else current_ask
            leg.buy_filled = True
            LOG.info("SCALP buy IMMEDIATE fill %s shares=%.4f @ %.3f", leg.token, fs, leg.filled_avg_px)

    async def on_book_tick(self, *, market_id: str, yes_ask: float | None,
                           yes_bid: float | None, no_ask: float | None,
                           no_bid: float | None, game_over: bool,
                           tick_size: str, neg_risk: bool) -> None:
        """Advance pair state on every book update."""
        pair = self._pairs.get(market_id)
        if pair is None or pair.closed: return

        is_paper = not ENABLE_REAL_LIVE_TRADING

        # 1) RETRY un-filled buys (live mode only — paper marks filled at open)
        if not is_paper:
            for leg, current_ask in ((pair.yes, yes_ask), (pair.no, no_ask)):
                if not leg.buy_filled and leg.buy_order_id is None \
                   and leg.buy_attempts < SCALP_MAX_RETRIES and current_ask is not None \
                   and self._client is not None:
                    await self._place_buy(pair, leg=leg, current_ask=current_ask,
                                           tick_size=tick_size, neg_risk=neg_risk)

        # 2) PAPER scratch — if bid hits entry+SCRATCH_CENTS, simulate fill
        if is_paper:
            self._paper_scratch_check(pair, yes_bid=yes_bid, no_bid=no_bid)
        else:
            # LIVE: place scratches once both buys filled
            if pair.yes.buy_filled and pair.no.buy_filled:
                if pair.yes.scratch_order_id is None and not pair.yes.scratch_filled:
                    await self._place_scratch(pair, leg=pair.yes, tick_size=tick_size, neg_risk=neg_risk)
                if pair.no.scratch_order_id is None and not pair.no.scratch_filled:
                    await self._place_scratch(pair, leg=pair.no, tick_size=tick_size, neg_risk=neg_risk)
            for leg in (pair.yes, pair.no):
                if leg.buy_filled and not leg.scratch_filled \
                   and leg.scratch_order_id is None and leg.scratch_attempts < SCALP_MAX_RETRIES \
                   and leg.scratch_attempts > 0:
                    await self._place_scratch(pair, leg=leg, tick_size=tick_size, neg_risk=neg_risk)

        # 3) STOP LOSS on un-scratched legs (paper + live, paper uses bid path)
        self._stop_loss_check(pair, yes_bid=yes_bid, no_bid=no_bid)

        # 4) Identify ride side
        if pair.ride_token is None:
            yes_done = pair.yes.scratch_filled or pair.yes.closed
            no_done = pair.no.scratch_filled or pair.no.closed
            if yes_done and not no_done:
                pair.ride_token = pair.no.token
            elif no_done and not yes_done:
                pair.ride_token = pair.yes.token

        # 5) Ride peak + trailing stop + take-profit
        if pair.ride_token is not None:
            ride_leg = pair.no if pair.ride_token == pair.no.token else pair.yes
            ride_bid = no_bid if pair.ride_token == pair.no.token else yes_bid
            if ride_bid is not None and ride_bid > pair.ride_peak_bid:
                pair.ride_peak_bid = ride_bid
            # Arm trail once peak crosses min-peak threshold
            if pair.ride_peak_bid >= SCALP_RIDE_TRAIL_MIN_PEAK:
                pair._ride_trail_armed = True  # type: ignore[attr-defined]
            if ride_bid is not None and not ride_leg.closed:
                close_reason = None
                if ride_bid >= SCALP_RIDE_TARGET:
                    close_reason = f"ride_tp_at_{ride_bid:.3f}"
                elif getattr(pair, "_ride_trail_armed", False) \
                     and ride_bid <= pair.ride_peak_bid - SCALP_RIDE_TRAIL_CENTS:
                    close_reason = f"ride_trail_peak{pair.ride_peak_bid:.3f}_exit{ride_bid:.3f}"
                if close_reason:
                    if is_paper:
                        self._paper_close_ride(pair, ride_leg=ride_leg, bid=ride_bid, reason=close_reason)
                    else:
                        await self._close_ride(pair, ride_leg=ride_leg, bid=ride_bid,
                                                tick_size=tick_size, neg_risk=neg_risk)

        # 6) Time-based exit
        age_min = (time.time_ns() - pair.opened_at_ns) / 60e9
        if age_min >= SCALP_MAX_HOLD_MIN and not pair.closed:
            self._paper_force_close(pair, yes_bid=yes_bid, no_bid=no_bid,
                                     reason=f"max_hold_{age_min:.0f}min")
            return

        # 7) Game over → force-close anything still open
        if game_over and not pair.closed:
            if is_paper:
                self._paper_force_close(pair, yes_bid=yes_bid, no_bid=no_bid,
                                         reason="game_over")
            else:
                await self._force_close_at_settle(pair, yes_bid=yes_bid, no_bid=no_bid,
                                                   tick_size=tick_size, neg_risk=neg_risk)

    def _paper_scratch_check(self, pair: ScalpPair, *, yes_bid, no_bid) -> None:
        """Simulate scratch fill when bid >= entry + SCRATCH_CENTS."""
        for leg, bid in ((pair.yes, yes_bid), (pair.no, no_bid)):
            if leg.scratch_filled or leg.closed: continue
            if bid is None: continue
            target = leg.entry_px + SCALP_SCRATCH_CENTS
            if bid >= target:
                self._record_scratch_fill(leg, fill_px=bid, shares=leg.filled_shares)

    def _stop_loss_check(self, pair: ScalpPair, *, yes_bid, no_bid) -> None:
        """Stop un-scratched legs when bid drops to entry - STOP_LOSS_CENTS."""
        for leg, bid in ((pair.yes, yes_bid), (pair.no, no_bid)):
            if leg.scratch_filled or leg.closed: continue
            if bid is None: continue
            stop_px = leg.entry_px - SCALP_STOP_LOSS_CENTS
            if bid <= stop_px:
                # Treat as a "scratched" but at a loss
                gross = (bid - leg.filled_avg_px) * leg.filled_shares
                fees = (leg.filled_avg_px + bid) * leg.filled_shares * _FEE_RATE
                leg.realized_pnl = gross - fees
                leg.closed = True
                leg.scratch_filled = True  # treat as closed for downstream logic
                leg.scratch_filled_px = bid
                LOG.info("SCALP STOP %s shares=%.4f @ %.3f pnl=%+.3f",
                          leg.token, leg.filled_shares, bid, leg.realized_pnl)

    def _paper_close_ride(self, pair: ScalpPair, *, ride_leg: ScalpLeg, bid: float, reason: str) -> None:
        """Simulate ride-leg close in paper mode."""
        gross = (bid - ride_leg.filled_avg_px) * ride_leg.filled_shares
        fees = (ride_leg.filled_avg_px + bid) * ride_leg.filled_shares * _FEE_RATE
        ride_leg.realized_pnl = gross - fees
        ride_leg.closed = True
        self._close_pair(pair, reason=reason)

    def _paper_force_close(self, pair: ScalpPair, *, yes_bid, no_bid, reason: str) -> None:
        """Force-close any still-open legs at current bid (paper)."""
        for leg, bid in ((pair.yes, yes_bid), (pair.no, no_bid)):
            if leg.closed or leg.scratch_filled: continue
            exit_px = bid if bid is not None and bid > 0 else 0.0
            gross = (exit_px - leg.filled_avg_px) * leg.filled_shares
            fees = (leg.filled_avg_px + exit_px) * leg.filled_shares * _FEE_RATE
            leg.realized_pnl = gross - fees
            leg.closed = True
        self._close_pair(pair, reason=reason)

    async def _place_scratch(self, pair: ScalpPair, *, leg: ScalpLeg,
                              tick_size: str, neg_risk: bool) -> None:
        if leg.filled_shares <= 0: return  # nothing to sell
        leg.scratch_attempts += 1
        price = round(leg.entry_px + SCALP_SCRATCH_CENTS, 3)
        # Use ACTUAL filled shares — never request more than we hold
        shares = round(leg.filled_shares, 4)
        try:
            resp = await self._client.sell_gtc_limit(
                token_id=leg.token, shares=shares, price_floor=price,
                tick_size=tick_size, neg_risk=neg_risk,
            )
        except Exception as exc:
            LOG.warning("SCALP scratch attempt %d failed %s: %s",
                         leg.scratch_attempts, leg.token, exc)
            return
        oid, fs, avg_px, status = _parse_order_resp(resp)
        if status in ("canceled", "rejected", "expired", "declined", "killed"):
            LOG.warning("SCALP scratch rejected %s: status=%s", leg.token, status)
            return  # leg.scratch_order_id stays None → retry on next tick
        leg.scratch_order_id = oid
        if fs > 0:
            # Immediate fill (bid was already there)
            self._record_scratch_fill(leg, fill_px=avg_px if avg_px > 0 else price, shares=fs)

    def _record_scratch_fill(self, leg: ScalpLeg, *, fill_px: float, shares: float) -> None:
        leg.scratch_filled = True
        leg.scratch_filled_px = fill_px
        # P&L = (sell_px - buy_px) * shares - fees
        gross = (fill_px - leg.filled_avg_px) * shares
        fees = (leg.filled_avg_px + fill_px) * shares * _FEE_RATE
        leg.realized_pnl = gross - fees
        leg.closed = True
        LOG.info("SCALP scratch FILLED %s shares=%.4f @ %.3f pnl=%+.3f",
                  leg.token, shares, fill_px, leg.realized_pnl)

    async def _close_ride(self, pair: ScalpPair, *, ride_leg: ScalpLeg,
                           bid: float, tick_size: str, neg_risk: bool) -> None:
        if ride_leg.filled_shares <= 0 or ride_leg.closed: return
        shares = round(ride_leg.filled_shares, 4)
        try:
            resp = await self._client.sell_gtc_limit(
                token_id=ride_leg.token, shares=shares, price_floor=bid,
                tick_size=tick_size, neg_risk=neg_risk,
            )
        except Exception as exc:
            LOG.warning("SCALP ride TP failed %s: %s", ride_leg.token, exc)
            return
        _, fs, avg_px, status = _parse_order_resp(resp)
        actual_px = avg_px if avg_px > 0 else bid
        # Even if status is 'live' (resting), treat as closed at target — the bid
        # was there, this should fill immediately.
        gross = (actual_px - ride_leg.filled_avg_px) * shares
        fees = (ride_leg.filled_avg_px + actual_px) * shares * _FEE_RATE
        ride_leg.realized_pnl = gross - fees
        ride_leg.closed = True
        self._close_pair(pair, reason=f"ride_tp_at_{actual_px:.3f}")
        LOG.info("SCALP ride CLOSED %s @ %.3f (peak %.3f) pnl=%+.3f",
                  ride_leg.token, actual_px, pair.ride_peak_bid, ride_leg.realized_pnl)

    async def _force_close_at_settle(self, pair: ScalpPair, *, yes_bid: float | None,
                                       no_bid: float | None, tick_size: str, neg_risk: bool) -> None:
        """Game over — sell any leftover legs at the current bid (or settle at 0/1)."""
        for leg, bid in ((pair.yes, yes_bid), (pair.no, no_bid)):
            if leg.closed or leg.filled_shares <= 0: continue
            sell_px = bid if bid and bid > 0 else 0.0
            if sell_px > 0 and self._client is not None:
                try:
                    await self._client.sell_gtc_limit(
                        token_id=leg.token, shares=round(leg.filled_shares, 4),
                        price_floor=sell_px, tick_size=tick_size, neg_risk=neg_risk,
                    )
                except Exception as exc:
                    LOG.warning("SCALP settle-close failed %s: %s", leg.token, exc)
            # Record P&L either way (best estimate)
            gross = (sell_px - leg.filled_avg_px) * leg.filled_shares
            fees = (leg.filled_avg_px + sell_px) * leg.filled_shares * _FEE_RATE
            leg.realized_pnl = gross - fees
            leg.closed = True
        self._close_pair(pair, reason="game_over")

    def _close_pair(self, pair: ScalpPair, *, reason: str) -> None:
        if pair.closed: return
        pair.closed = True
        pair.close_reason = reason
        pair.closed_at_ns = time.time_ns()
        self._log_pair_to_csv(pair)
        total = pair.realized_pnl_usd
        LOG.info("SCALP pair %s CLOSED reason=%s total_pnl=%+.3f",
                  pair.market_id, reason, total)
        # Decrement series counter
        series = getattr(pair, "_series_key", "") or ""
        if series and self._series_open_count.get(series, 0) > 0:
            self._series_open_count[series] -= 1
        # Cooldown on loss
        if total < 0:
            self._cooldown_until_ns = time.time_ns() + int(SCALP_COOLDOWN_AFTER_LOSS_SEC * 1e9)
            LOG.warning("SCALP cooldown %ds after loss %+.2f", SCALP_COOLDOWN_AFTER_LOSS_SEC, total)

    def _log_pair_to_csv(self, pair: ScalpPair) -> None:
        path = Path(SCALP_TRADES_CSV_PATH)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        write_header = not path.exists() or path.stat().st_size == 0
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        row = {
            "timestamp_utc": ts, "market_id": pair.market_id, "match_id": pair.match_id,
            "yes_entry_px": pair.yes.entry_px, "no_entry_px": pair.no.entry_px,
            "yes_filled_shares": pair.yes.filled_shares, "no_filled_shares": pair.no.filled_shares,
            "yes_filled_avg_px": pair.yes.filled_avg_px, "no_filled_avg_px": pair.no.filled_avg_px,
            "yes_scratch_filled_px": pair.yes.scratch_filled_px,
            "no_scratch_filled_px": pair.no.scratch_filled_px,
            "ride_token": pair.ride_token or "",
            "ride_peak_bid": pair.ride_peak_bid,
            "yes_pnl": pair.yes.realized_pnl, "no_pnl": pair.no.realized_pnl,
            "total_pnl_usd": pair.realized_pnl_usd,
            "close_reason": pair.close_reason,
            "duration_sec": (pair.closed_at_ns - pair.opened_at_ns) / 1e9 if pair.closed_at_ns else 0.0,
        }
        try:
            with path.open("a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(row.keys()))
                if write_header: w.writeheader()
                w.writerow(row)
        except Exception as exc:
            LOG.warning("SCALP csv log failed: %s", exc)

    async def poll_fills(self, check_gtc_fill) -> None:
        """Poll outstanding orders. Updates filled state with ACTUAL share counts."""
        if not self._pairs: return
        for market_id, pair in list(self._pairs.items()):
            if pair.closed: continue
            # Buy fills
            for leg in (pair.yes, pair.no):
                if leg.buy_order_id and not leg.buy_filled:
                    try: resp = await check_gtc_fill(leg.buy_order_id)
                    except Exception: continue
                    _, fs, avg_px, status = _parse_order_resp(resp)
                    if status in ("matched", "filled") and fs > 0:
                        leg.filled_shares = fs
                        leg.filled_avg_px = avg_px if avg_px > 0 else leg.entry_px
                        leg.buy_filled = True
                        LOG.info("SCALP buy fill polled %s shares=%.4f @ %.3f",
                                  leg.token, fs, leg.filled_avg_px)
                    elif status in ("canceled", "killed", "rejected", "expired", "declined"):
                        leg.buy_order_id = None  # opens retry path in on_book_tick
                        LOG.warning("SCALP buy %s status=%s → will retry", leg.token, status)
            # Scratch fills
            for leg in (pair.yes, pair.no):
                if leg.scratch_order_id and not leg.scratch_filled:
                    try: resp = await check_gtc_fill(leg.scratch_order_id)
                    except Exception: continue
                    _, fs, avg_px, status = _parse_order_resp(resp)
                    if status in ("matched", "filled") and fs > 0:
                        self._record_scratch_fill(
                            leg,
                            fill_px=avg_px if avg_px > 0 else (leg.entry_px + SCALP_SCRATCH_CENTS),
                            shares=fs,
                        )
                    elif status in ("canceled", "killed", "rejected", "expired", "declined"):
                        leg.scratch_order_id = None  # retry on next on_book_tick
                        LOG.warning("SCALP scratch %s status=%s → will retry", leg.token, status)
