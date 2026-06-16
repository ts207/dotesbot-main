"""One-off: re-run backtest with FIGHT_SWING and VALUE_DISAGREEMENT caps raised
so we can see what's hiding in the price buckets currently rejected.

Outputs the same per-event PnL aggregation the main run produced.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import signal_engine

# Monkey-patch the caps before backtest sees them. 0.95 is the highest
# "non-terminal" we'd ever consider; below the 0.95 chasing-terminal guard.
signal_engine._EVENT_MAX_FILL["POLL_FIGHT_SWING"] = 0.94
signal_engine._EVENT_MAX_FILL["POLL_VALUE_DISAGREEMENT"] = 0.85

# Make sure backtest's import picks up the new value.
import backtest_live_data
backtest_live_data._EVENT_MAX_FILL = signal_engine._EVENT_MAX_FILL

# Now drive the backtest CLI as a normal script.
sys.argv = [
    "backtest_live_data.py",
    "--min-lag", "0.05",
    "--min-edge", "0.05",
    "--max-spread", "0.15",
    "--size", "5",
    "--exit", "30",
    "--max-book-age", "90000",
    "--diagnostics",
    "--csv-out", str(ROOT / "validations" / "backtest_2026_05_25_relaxed.csv"),
]
backtest_live_data.main = backtest_live_data.__dict__.get("main")
# main isn't defined as a function — the script runs at import time.
# Re-execute the module body using runpy.
import runpy
runpy.run_module("backtest_live_data", run_name="__main__")
