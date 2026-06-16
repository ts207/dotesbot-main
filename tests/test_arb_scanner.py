"""Unit tests for arb_scanner.scan_pair."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from arb_scanner import (
    scan_pair, ArbOpportunity, ArbReject,
    ARB_LEG_SIZE_USD, ARB_MIN_PROFIT_CENTS,
)


def _mapping(market_id="MKT1", match_id="12345", yes="YES_TOK", no="NO_TOK"):
    return {"market_id": market_id, "dota_match_id": match_id,
            "yes_token_id": yes, "no_token_id": no}


def _book(*, ask, ask_size=1000, bid=None):
    if bid is None:
        bid = max(ask - 0.01, 0.0)
    return {"best_ask": ask, "best_bid": bid,
            "ask_size": ask_size, "bid_size": ask_size}


# --- positive case ---
def test_detects_clear_arb_opportunity():
    res = scan_pair(
        yes_book=_book(ask=0.40),
        no_book =_book(ask=0.50),
        mapping=_mapping(),
        received_at_ns=1,
        leg_size_usd=5.0,
        min_profit_cents=1.5,
    )
    assert isinstance(res, ArbOpportunity)
    assert res.arb_cost == pytest.approx(0.90)
    assert res.profit_cents == pytest.approx(10.0)
    assert res.profit_per_dollar == pytest.approx(0.10 / 0.90)
    assert res.expected_profit_usd > 0


def test_arb_id_is_deterministic():
    a = scan_pair(yes_book=_book(ask=0.40), no_book=_book(ask=0.50),
                  mapping=_mapping(), received_at_ns=42)
    b = scan_pair(yes_book=_book(ask=0.40), no_book=_book(ask=0.50),
                  mapping=_mapping(), received_at_ns=42)
    assert isinstance(a, ArbOpportunity) and isinstance(b, ArbOpportunity)
    assert a.arb_id == b.arb_id


# --- rejects ---
def test_rejects_when_sum_above_one():
    res = scan_pair(yes_book=_book(ask=0.55), no_book=_book(ask=0.55),
                    mapping=_mapping(), received_at_ns=1)
    assert isinstance(res, ArbReject)
    assert res.reason == "below_min_profit"


def test_rejects_when_profit_below_floor():
    # ask sum = 0.99 → 1c profit, below default 1.5c floor
    res = scan_pair(yes_book=_book(ask=0.49), no_book=_book(ask=0.50),
                    mapping=_mapping(), received_at_ns=1,
                    min_profit_cents=1.5)
    assert isinstance(res, ArbReject)
    assert res.reason == "below_min_profit"


def test_passes_when_profit_above_floor():
    res = scan_pair(yes_book=_book(ask=0.49), no_book=_book(ask=0.50),
                    mapping=_mapping(), received_at_ns=1,
                    min_profit_cents=0.5)  # lower floor
    assert isinstance(res, ArbOpportunity)


def test_rejects_when_yes_book_missing():
    res = scan_pair(yes_book=None, no_book=_book(ask=0.50),
                    mapping=_mapping(), received_at_ns=1)
    assert isinstance(res, ArbReject)
    assert res.reason == "incomplete_book"


def test_rejects_when_no_book_missing_ask():
    res = scan_pair(yes_book=_book(ask=0.40),
                    no_book={"best_ask": None, "best_bid": 0.49, "ask_size": 100, "bid_size": 100},
                    mapping=_mapping(), received_at_ns=1)
    assert isinstance(res, ArbReject)
    assert res.reason == "missing_ask"


# --- depth check ---
def test_rejects_when_yes_ask_size_insufficient():
    # ask=0.40, leg=$5 → 12.5 shares needed.
    # With max_fraction 0.5, need ask_size >= 25. Set ask_size=20 → reject.
    res = scan_pair(yes_book=_book(ask=0.40, ask_size=20),
                    no_book =_book(ask=0.50, ask_size=10000),
                    mapping=_mapping(), received_at_ns=1,
                    leg_size_usd=5.0)
    assert isinstance(res, ArbReject)
    assert res.reason == "yes_ask_size_insufficient"


def test_size_check_skipped_when_size_missing():
    res = scan_pair(yes_book={"best_ask": 0.40, "best_bid": 0.39,
                              "ask_size": None, "bid_size": None},
                    no_book =_book(ask=0.50),
                    mapping=_mapping(), received_at_ns=1)
    assert isinstance(res, ArbOpportunity)


# --- field fidelity ---
def test_opportunity_carries_pair_identity():
    res = scan_pair(yes_book=_book(ask=0.40), no_book=_book(ask=0.50),
                    mapping=_mapping(market_id="MKT_X", match_id="9999",
                                     yes="YT", no="NT"),
                    received_at_ns=42)
    assert isinstance(res, ArbOpportunity)
    assert res.market_id == "MKT_X"
    assert res.match_id == "9999"
    assert res.yes_token_id == "YT"
    assert res.no_token_id == "NT"


# --- expected profit math sanity ---
def test_expected_profit_scales_with_leg_size():
    res5 = scan_pair(yes_book=_book(ask=0.40), no_book=_book(ask=0.50),
                     mapping=_mapping(), received_at_ns=1, leg_size_usd=5.0)
    res10 = scan_pair(yes_book=_book(ask=0.40), no_book=_book(ask=0.50),
                      mapping=_mapping(), received_at_ns=1, leg_size_usd=10.0)
    assert isinstance(res5, ArbOpportunity) and isinstance(res10, ArbOpportunity)
    assert res10.expected_profit_usd == pytest.approx(2 * res5.expected_profit_usd)


def test_matched_shares_math_yields_equal_share_counts():
    """The whole point of the AR-3 fix: yes_usd and no_usd should buy
    EQUAL shares on each side, guaranteeing $1 payout at settle."""
    res = scan_pair(
        yes_book=_book(ask=0.40), no_book=_book(ask=0.50),
        mapping=_mapping(), received_at_ns=1,
        total_capital_usd=10.0,
    )
    assert isinstance(res, ArbOpportunity)
    yes_shares = res.yes_usd / res.yes_ask
    no_shares  = res.no_usd  / res.no_ask
    assert yes_shares == pytest.approx(no_shares)
    assert yes_shares == pytest.approx(res.shares_per_side)
    # Total spend = total_capital
    assert (res.yes_usd + res.no_usd) == pytest.approx(10.0)
    # Settlement: ONE side pays $1 per share. Profit is guaranteed.
    assert res.expected_profit_usd == pytest.approx(res.shares_per_side - 10.0)


def test_total_capital_usd_param_overrides_default():
    res = scan_pair(yes_book=_book(ask=0.30), no_book=_book(ask=0.40),
                    mapping=_mapping(), received_at_ns=1,
                    total_capital_usd=100.0, min_profit_cents=0.5)
    assert isinstance(res, ArbOpportunity)
    assert res.total_capital_usd == 100.0
    assert (res.yes_usd + res.no_usd) == pytest.approx(100.0)
