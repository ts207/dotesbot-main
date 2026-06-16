"""Tests for the dual-sided scalp improvements (SC-1 cross-book gate +
SC-2 per-token lock).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scalp_executor import ScalpExecutor


def _book(*, bid, ask, bid_size=10000, ask_size=10000):
    return {"best_bid": bid, "best_ask": ask,
            "bid_size": bid_size, "ask_size": ask_size}


# --- SC-1 cross-book gate ---
def test_cross_book_disagreement_blocks_scalp():
    # YES_mid = 0.50, NO_mid = 0.45 → implied YES via NO = 0.55, disagree = 5c
    yes = _book(bid=0.49, ask=0.51)
    no  = _book(bid=0.44, ask=0.46)
    ok, why = ScalpExecutor.qualifies(
        yes_ask=0.51, no_ask=0.46,
        yes_book=yes, no_book=no,
        game_time_sec=120,
    )
    assert ok is False
    assert "cross_book_disagreement" in why


def test_cross_book_agreement_lets_scalp_through():
    # YES_mid = 0.50, NO_mid = 0.50 → disagreement 0c
    yes = _book(bid=0.49, ask=0.51)
    no  = _book(bid=0.49, ask=0.51)
    ok, _why = ScalpExecutor.qualifies(
        yes_ask=0.51, no_ask=0.51,
        yes_book=yes, no_book=no,
        game_time_sec=120,
    )
    # Note: this still has to pass the existing skew/sum/depth gates which
    # this configuration does. We're verifying the SC-1 gate doesn't reject
    # an agreeing market.
    assert ok is True


def test_cross_book_gate_skipped_when_books_missing():
    """If only asks are provided (legacy callers without book dicts), the
    gate must not panic — it should just rely on the existing skew/sum logic."""
    ok, _why = ScalpExecutor.qualifies(
        yes_ask=0.51, no_ask=0.49,
        yes_book=None, no_book=None,
        game_time_sec=120,
    )
    # Skew = 0.02, sum = 1.00 — should pass.
    assert ok is True


def test_cross_book_gate_distinguishes_above_below_2c_threshold():
    # 1c disagreement passes; 3c fails. (Exact 2c is on the FP boundary
    # and is intentionally not tested — production behavior either way
    # is acceptable for a 2c heuristic.)
    yes = _book(bid=0.49, ask=0.51)              # YES_mid 0.50
    no_close = _book(bid=0.48, ask=0.50)         # NO_mid 0.49 → implied YES 0.51, disagree 1c
    ok1, _ = ScalpExecutor.qualifies(
        yes_ask=0.51, no_ask=0.50,
        yes_book=yes, no_book=no_close,
        game_time_sec=120,
    )
    assert ok1 is True

    no_far = _book(bid=0.46, ask=0.48)           # NO_mid 0.47 → implied YES 0.53, disagree 3c
    ok2, why = ScalpExecutor.qualifies(
        yes_ask=0.51, no_ask=0.48,
        yes_book=yes, no_book=no_far,
        game_time_sec=120,
    )
    assert ok2 is False
    assert "cross_book_disagreement" in why
