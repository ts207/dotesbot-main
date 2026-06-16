"""One-off: backtest with POLL_DECISIVE_STOMP and POLL_RAPID_STOMP promoted
to the live allowlist. These fire heavily in production (381 + 154 events
historically) and are research-tier today because shadow trades showed
negative @30s markouts — but the relaxed-caps + hold-to-settle posture might
flip them positive.

Doesn't write to event_taxonomy.py; just patches the backtest's allowed set
and caps in-memory.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import signal_engine
signal_engine._EVENT_MAX_FILL["POLL_FIGHT_SWING"] = 0.94
signal_engine._EVENT_MAX_FILL["POLL_VALUE_DISAGREEMENT"] = 0.85
# Stomp variants: keep their existing tight caps but lift acceptance.
import config
allowed = set(config.TRADE_EVENTS) | {"POLL_DECISIVE_STOMP", "POLL_RAPID_STOMP"}

import backtest_live_data as bt
diag = Counter()
trades, n_eval = bt.run_backtest(
    min_lag=0.05, min_edge=0.05, max_spread=0.15, size_usd=5,
    exit_sec=30, max_book_age_ms=90000,
    diagnostics=diag,
    trade_events=allowed,
)

# Write trade CSV
out_path = ROOT / "validations" / "backtest_2026_05_26_stomps_promoted.csv"
import csv
with out_path.open("w", encoding="utf-8", newline="") as f:
    if trades:
        # Use first trade's __dict__ keys as columns.
        cols = list(vars(trades[0]).keys())
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        for t in trades:
            writer.writerow(vars(t))
print(f"wrote {len(trades)} trades to {out_path}")
