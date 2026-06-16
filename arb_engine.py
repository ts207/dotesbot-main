"""Stateful wrapper around `arb_scanner.scan_pair`.

Scans every tracked market on each tick; rejects opportunities for markets
that already have an open arb position. Logs every decision (signal or
reject) so the data layer can audit pass-through rate.
"""
from __future__ import annotations

import os
import time
from typing import Any, Mapping, Sequence

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from arb_scanner import (
    scan_pair, ArbOpportunity, ArbReject,
    ARB_LEG_SIZE_USD, ARB_MIN_PROFIT_CENTS,
)

ARB_ENGINE_ENABLED = os.getenv("ARB_ENGINE_ENABLED", "false").lower() in {"1", "true", "yes"}
ENABLE_ARB_TRADING = os.getenv("ENABLE_ARB_TRADING", "false").lower() in {"1", "true", "yes"}
ARB_MAX_OPEN_POSITIONS = int(os.getenv("ARB_MAX_OPEN_POSITIONS", "5"))
# Time window during which we won't re-fire the same market after rejecting
# it (avoids logging the same below-min-profit state every tick).
ARB_REJECT_COOLDOWN_SEC = int(os.getenv("ARB_REJECT_COOLDOWN_SEC", "10"))


class ArbEngine:
    """Pull-model scanner. Caller invokes `scan_all(book_store)` every
    snapshot tick (or every N seconds); engine returns whatever fires.

    Open arbs are tracked via `mark_arb_opened(market_id)` /
    `mark_arb_closed(market_id)`. Markets with open arbs are skipped.
    """

    def __init__(
        self,
        mappings: Sequence[Mapping] | Mapping[str, Mapping],
        *,
        leg_size_usd: float = ARB_LEG_SIZE_USD,
        min_profit_cents: float = ARB_MIN_PROFIT_CENTS,
    ):
        self._mappings: list[dict] = []
        if isinstance(mappings, Mapping):
            for v in mappings.values():
                self._mappings.append(dict(v))
        else:
            for m in mappings:
                if m.get("yes_token_id") and m.get("no_token_id"):
                    self._mappings.append(dict(m))

        self.leg_size_usd = float(leg_size_usd)
        self.min_profit_cents = float(min_profit_cents)
        self._open_arb_market_ids: set[str] = set()
        self._last_reject_ns: dict[str, int] = {}
        # Operational stats.
        self._scan_count = 0
        self._opp_count = 0
        self._reject_counts: dict[str, int] = {}

    # ---------- public API ----------
    def scan_all(self, book_store: Any) -> list[ArbOpportunity | ArbReject]:
        """Iterate over all tracked markets and score each pair. Returns
        only the results worth logging — opportunities and *fresh*
        rejects (cooldown-filtered)."""
        now_ns = time.time_ns()
        out: list[ArbOpportunity | ArbReject] = []
        for m in self._mappings:
            market_id = str(m.get("market_id") or "")
            if market_id in self._open_arb_market_ids:
                continue

            yes_book = book_store.get(m["yes_token_id"]) if book_store else None
            no_book  = book_store.get(m["no_token_id"])  if book_store else None
            self._scan_count += 1

            res = scan_pair(
                yes_book=yes_book, no_book=no_book,
                mapping=m, received_at_ns=now_ns,
                leg_size_usd=self.leg_size_usd,
                min_profit_cents=self.min_profit_cents,
            )
            if isinstance(res, ArbOpportunity):
                self._opp_count += 1
                out.append(res)
                continue

            # ArbReject: dedupe via cooldown so we don't spam logs.
            last = self._last_reject_ns.get(market_id, 0)
            if (now_ns - last) / 1e9 < ARB_REJECT_COOLDOWN_SEC:
                continue
            self._last_reject_ns[market_id] = now_ns
            self._reject_counts[res.reason] = self._reject_counts.get(res.reason, 0) + 1
            out.append(res)
        return out

    def mark_arb_opened(self, market_id: str) -> None:
        self._open_arb_market_ids.add(market_id)

    def mark_arb_closed(self, market_id: str) -> None:
        self._open_arb_market_ids.discard(market_id)

    def open_arb_count(self) -> int:
        return len(self._open_arb_market_ids)

    def can_open_another(self) -> bool:
        return self.open_arb_count() < ARB_MAX_OPEN_POSITIONS

    def refresh_mappings(self, mappings: Sequence[Mapping] | Mapping[str, Mapping]) -> None:
        old_open = self._open_arb_market_ids.copy()
        new: list[dict] = []
        if isinstance(mappings, Mapping):
            for v in mappings.values():
                new.append(dict(v))
        else:
            for m in mappings:
                if m.get("yes_token_id") and m.get("no_token_id"):
                    new.append(dict(m))
        self._mappings = new
        # Keep open-arb tracking for any market still present; drop the rest.
        live_market_ids = {str(m.get("market_id") or "") for m in new}
        self._open_arb_market_ids = old_open & live_market_ids

    def stats(self) -> dict:
        return {
            "scans": self._scan_count,
            "opportunities": self._opp_count,
            "rejects_by_reason": dict(self._reject_counts),
            "open_arbs": self.open_arb_count(),
            "tracked_markets": len(self._mappings),
        }
