"""Tests for ArbEngine — stateful scanning + open-arb tracking."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from arb_engine import ArbEngine
from arb_scanner import ArbOpportunity, ArbReject


class FakeBookStore:
    def __init__(self, books=None):
        self._books = books or {}
    def get(self, asset_id):
        return self._books.get(asset_id)
    def set(self, asset_id, *, bid, ask, ask_size=10000):
        self._books[asset_id] = {"best_bid": bid, "best_ask": ask,
                                  "ask_size": ask_size, "bid_size": ask_size,
                                  "received_at_ns": time.time_ns()}


def _mapping(market_id="MKT1", match_id="12345", yes="Y1", no="N1"):
    return {"market_id": market_id, "dota_match_id": match_id,
            "yes_token_id": yes, "no_token_id": no}


def test_scan_returns_opportunity_when_books_arb_friendly():
    engine = ArbEngine([_mapping()], leg_size_usd=5.0, min_profit_cents=1.0)
    store = FakeBookStore()
    store.set("Y1", bid=0.39, ask=0.40)
    store.set("N1", bid=0.49, ask=0.50)
    results = engine.scan_all(store)
    assert len(results) == 1
    assert isinstance(results[0], ArbOpportunity)
    assert results[0].profit_cents == pytest.approx(10.0)


def test_scan_skips_market_with_open_arb():
    engine = ArbEngine([_mapping()], min_profit_cents=1.0)
    store = FakeBookStore()
    store.set("Y1", bid=0.39, ask=0.40)
    store.set("N1", bid=0.49, ask=0.50)
    engine.mark_arb_opened("MKT1")
    results = engine.scan_all(store)
    assert results == []


def test_reject_cooldown_dedupes_consecutive_misses(monkeypatch):
    # Set cooldown to a long value so the second scan should NOT emit.
    monkeypatch.setattr("arb_engine.ARB_REJECT_COOLDOWN_SEC", 10)
    engine = ArbEngine([_mapping()], min_profit_cents=2.0)
    store = FakeBookStore()
    store.set("Y1", bid=0.49, ask=0.50)
    store.set("N1", bid=0.49, ask=0.50)
    first = engine.scan_all(store)
    second = engine.scan_all(store)
    assert len(first) == 1 and isinstance(first[0], ArbReject)
    # Within the cooldown window we suppress.
    assert second == []


def test_multiple_mappings_scanned_independently():
    engine = ArbEngine([
        _mapping(market_id="MKT1", yes="Y1", no="N1"),
        _mapping(market_id="MKT2", yes="Y2", no="N2"),
    ], min_profit_cents=1.0)
    store = FakeBookStore()
    store.set("Y1", bid=0.39, ask=0.40)
    store.set("N1", bid=0.49, ask=0.50)
    # MKT2 books too expensive → reject
    store.set("Y2", bid=0.55, ask=0.56)
    store.set("N2", bid=0.55, ask=0.56)
    results = engine.scan_all(store)
    opps = [r for r in results if isinstance(r, ArbOpportunity)]
    rejects = [r for r in results if isinstance(r, ArbReject)]
    assert len(opps) == 1 and opps[0].market_id == "MKT1"
    assert any(r.market_id == "MKT2" for r in rejects)


def test_can_open_another_respects_max(monkeypatch):
    monkeypatch.setattr("arb_engine.ARB_MAX_OPEN_POSITIONS", 2)
    engine = ArbEngine([_mapping()])
    assert engine.can_open_another() is True
    engine.mark_arb_opened("A")
    engine.mark_arb_opened("B")
    assert engine.can_open_another() is False
    engine.mark_arb_closed("A")
    assert engine.can_open_another() is True


def test_refresh_mappings_preserves_open_tracking_for_existing_markets():
    engine = ArbEngine([_mapping(market_id="MKT1"), _mapping(market_id="MKT2", yes="Y2", no="N2")])
    engine.mark_arb_opened("MKT1")
    engine.mark_arb_opened("MKT2")
    engine.refresh_mappings([_mapping(market_id="MKT1")])  # drop MKT2
    assert "MKT1" in engine._open_arb_market_ids
    assert "MKT2" not in engine._open_arb_market_ids


def test_stats_track_counts():
    engine = ArbEngine([_mapping()], min_profit_cents=1.0)
    store = FakeBookStore()
    store.set("Y1", bid=0.39, ask=0.40)
    store.set("N1", bid=0.49, ask=0.50)
    engine.scan_all(store)
    s = engine.stats()
    assert s["scans"] == 1
    assert s["opportunities"] == 1
    assert s["tracked_markets"] == 1
