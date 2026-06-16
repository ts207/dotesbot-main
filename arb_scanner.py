"""Pure arbitrage scanner.

Polymarket binary markets pay $1 to exactly one side at settlement. When
`YES_ask + NO_ask < $1.00`, buying one share of each side gives a
guaranteed $1 at settlement minus the combined ask cost — pure arbitrage
modulo gas and execution slippage.

This module is the pure scoring function. Stateful wiring (per-pair
last-fired tracking, budget gates, executor calls) lives in `arb_engine.py`.

Provenance: dual-sided data study on data_v2 (60,869 synced YES/NO pairs)
found 605 opportunities across 85 of 118 matches, median 1.01% per-dollar
profit. Threshold floor at 1.5c gas+slippage covers the smallest arbs.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Mapping

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Floors and limits (env-tunable).
# ARB_TOTAL_CAPITAL_USD is the total dollars deployed per arb (split across
# both legs in a matched-shares ratio). Replaces the older per-leg sizing
# that produced unequal share counts and directional risk.
ARB_TOTAL_CAPITAL_USD = float(os.getenv("ARB_TOTAL_CAPITAL_USD", "10.0"))
ARB_MIN_PROFIT_CENTS = float(os.getenv("ARB_MIN_PROFIT_CENTS", "1.5"))
ARB_MAX_ASK_SIZE_FRACTION = float(os.getenv("ARB_MAX_ASK_SIZE_FRACTION", "0.5"))

# Back-compat alias — some callers still pass ARB_LEG_SIZE_USD.
ARB_LEG_SIZE_USD = ARB_TOTAL_CAPITAL_USD / 2.0

# Deterministic UUID namespace for arb_ids.
_NAMESPACE = uuid.UUID("99999999-aaaa-bbbb-cccc-dddddddddddd")


@dataclass(frozen=True)
class ArbOpportunity:
    """A scored YES+NO arb. Carries everything an executor needs to submit
    both legs and a logger needs to audit the decision."""
    arb_id: str
    market_id: str
    match_id: str
    yes_token_id: str
    no_token_id: str
    received_at_ns: int
    # pricing
    yes_ask: float
    no_ask: float
    yes_ask_size: float | None
    no_ask_size: float | None
    arb_cost: float                # yes_ask + no_ask
    profit_per_dollar: float       # (1 - arb_cost) / arb_cost
    profit_cents: float            # (1 - arb_cost) * 100
    # Matched-shares sizing. Total deployment = total_capital_usd, split into
    # yes_usd and no_usd so we end up with EQUAL shares on each side and
    # guaranteed $1 payout at settlement.
    total_capital_usd: float
    shares_per_side: float
    yes_usd: float                 # = shares × yes_ask
    no_usd: float                  # = shares × no_ask
    # Back-compat field — kept so existing live_executor code that reads
    # `opportunity.leg_size_usd` still works. Equal to total_capital_usd / 2.
    leg_size_usd: float
    # Profit at settlement (guaranteed): total_capital_usd × (1 - arb_cost) / arb_cost
    expected_profit_usd: float


@dataclass(frozen=True)
class ArbReject:
    """Returned when no arb is present. Carries the reason for forensic logs."""
    market_id: str
    match_id: str
    received_at_ns: int
    reason: str
    arb_cost: float | None = None


def _make_arb_id(market_id: str, received_at_ns: int) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"arb|{market_id}|{received_at_ns}"))


def scan_pair(
    *,
    yes_book: Mapping | None,
    no_book: Mapping | None,
    mapping: Mapping,
    received_at_ns: int,
    leg_size_usd: float | None = None,        # back-compat — sums to total_capital
    total_capital_usd: float | None = None,
    min_profit_cents: float = ARB_MIN_PROFIT_CENTS,
) -> ArbOpportunity | ArbReject:
    """Score one (YES book, NO book) pair against a market mapping.

    Returns an `ArbOpportunity` to execute (both legs FAK), or an `ArbReject`
    explaining why no arb is available right now.
    """
    market_id = str(mapping.get("market_id") or "")
    match_id = str(mapping.get("dota_match_id") or "")

    # Resolve sizing. `total_capital_usd` is the new canonical parameter; if
    # the caller passes `leg_size_usd` we treat it as half (back-compat).
    if total_capital_usd is None:
        if leg_size_usd is None:
            total_capital_usd = ARB_TOTAL_CAPITAL_USD
        else:
            total_capital_usd = float(leg_size_usd) * 2.0

    if not yes_book or not no_book:
        return ArbReject(market_id, match_id, received_at_ns, "incomplete_book")

    yes_ask = yes_book.get("best_ask")
    no_ask = no_book.get("best_ask")
    if yes_ask is None or no_ask is None:
        return ArbReject(market_id, match_id, received_at_ns, "missing_ask")

    arb_cost = float(yes_ask) + float(no_ask)
    profit_cents = (1.0 - arb_cost) * 100.0

    if profit_cents < float(min_profit_cents):
        return ArbReject(market_id, match_id, received_at_ns,
                         "below_min_profit", arb_cost)

    # Matched-shares sizing: total_capital_usd × (1/arb_cost) shares, split
    # across the two sides so we end up with EQUAL share counts.
    shares = total_capital_usd / max(arb_cost, 0.01)
    yes_usd = shares * float(yes_ask)
    no_usd = shares * float(no_ask)

    # Depth check: don't try to consume more than ARB_MAX_ASK_SIZE_FRACTION of
    # the visible ask on either side.
    yes_ask_size = yes_book.get("ask_size")
    no_ask_size = no_book.get("ask_size")
    if yes_ask_size is not None and shares > yes_ask_size * float(ARB_MAX_ASK_SIZE_FRACTION):
        return ArbReject(market_id, match_id, received_at_ns,
                         "yes_ask_size_insufficient", arb_cost)
    if no_ask_size is not None and shares > no_ask_size * float(ARB_MAX_ASK_SIZE_FRACTION):
        return ArbReject(market_id, match_id, received_at_ns,
                         "no_ask_size_insufficient", arb_cost)

    profit_per_dollar = (1.0 - arb_cost) / arb_cost if arb_cost > 0 else 0.0

    return ArbOpportunity(
        arb_id=_make_arb_id(market_id, received_at_ns),
        market_id=market_id,
        match_id=match_id,
        yes_token_id=str(mapping.get("yes_token_id") or ""),
        no_token_id=str(mapping.get("no_token_id") or ""),
        received_at_ns=received_at_ns,
        yes_ask=float(yes_ask),
        no_ask=float(no_ask),
        yes_ask_size=float(yes_ask_size) if yes_ask_size is not None else None,
        no_ask_size=float(no_ask_size) if no_ask_size is not None else None,
        arb_cost=arb_cost,
        profit_per_dollar=profit_per_dollar,
        profit_cents=profit_cents,
        total_capital_usd=float(total_capital_usd),
        shares_per_side=shares,
        yes_usd=yes_usd,
        no_usd=no_usd,
        leg_size_usd=float(total_capital_usd) / 2.0,    # back-compat alias
        expected_profit_usd=float(total_capital_usd) * profit_per_dollar,
    )
