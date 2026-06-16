"""Sweep STRUCTURAL_DOMINANCE thresholds. The candidate function lives in
event_detector; monkey-patch its constants and re-run for each setting.
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import signal_engine
signal_engine._EVENT_MAX_FILL["POLL_FIGHT_SWING"] = 0.94
signal_engine._EVENT_MAX_FILL["POLL_VALUE_DISAGREEMENT"] = 0.85

import event_detector
import backtest_live_data as bt

# Monkey-patch the function to accept tunable thresholds via closure.
ORIG_FN = event_detector.EventDetector._structural_dominance_candidates

def patched(struct_th, nw_th, kill_th):
    def fn(self, delta, components, mapping):
        cur = delta.current
        s = cur.get("structure_state")
        if s is None or getattr(s, "confidence", 0.0) < 0.8:
            return []
        rad_fields = (s.radiant_t1_alive, s.radiant_t2_alive, s.radiant_t3_alive, s.radiant_t4_alive)
        dire_fields = (s.dire_t1_alive, s.dire_t2_alive, s.dire_t3_alive, s.dire_t4_alive)
        if any(f is None for f in rad_fields + dire_fields):
            return []
        rad_towers = sum(rad_fields)
        dire_towers = sum(dire_fields)
        struct_diff = rad_towers - dire_towers
        nw_lead = cur.get("radiant_lead")
        rad_score = cur.get("radiant_score")
        dire_score = cur.get("dire_score")
        if nw_lead is None or rad_score is None or dire_score is None:
            return []
        kill_lead = rad_score - dire_score
        game_time = cur.get("game_time_sec")
        if game_time is None or game_time < 600:
            return []
        if struct_diff >= struct_th and nw_lead >= nw_th and kill_lead >= kill_th:
            direction = "radiant"
        elif struct_diff <= -struct_th and nw_lead <= -nw_th and kill_lead <= -kill_th:
            direction = "dire"
        else:
            return []
        from event_detector import _components_for_direction
        return [self._event_from_components(
            "POLL_STRUCTURAL_DOMINANCE", direction, delta, mapping,
            _components_for_direction(components, direction, {"NETWORTH_DELTA", "KILL_DIFF_DELTA"}),
            previous_value="contested", current_value="dominated",
            event_delta=abs(nw_lead), threshold=nw_th, severity="high",
        )]
    return fn

SETTINGS = [
    ("current",  3, 5000, 4),
    ("loose-1",  2, 3500, 3),
    ("loose-2",  2, 2500, 2),
    ("strict-2", 4, 7000, 6),
]

print(f"{'label':10} {'struct':>6} {'nw':>5} {'kill':>4} {'seen':>5} {'acc':>4} {'mean@S':>8} {'win%':>5}")
for label, st, nw, kl in SETTINGS:
    event_detector.EventDetector._structural_dominance_candidates = patched(st, nw, kl)
    diag = Counter()
    trades, _ = bt.run_backtest(
        min_lag=0.05, min_edge=0.05, max_spread=0.15, size_usd=5,
        exit_sec=30, max_book_age_ms=90000, diagnostics=diag,
    )
    seen = diag.get("event_seen:POLL_STRUCTURAL_DOMINANCE", 0)
    sd_trades = [t for t in trades if t.event_type == "POLL_STRUCTURAL_DOMINANCE"]
    n = len(sd_trades)
    pnl_vals = [t.pnl_settle for t in sd_trades if t.pnl_settle is not None]
    mean = sum(pnl_vals)/len(pnl_vals) if pnl_vals else 0
    wins = sum(1 for v in pnl_vals if v > 0)
    win_pct = wins/len(pnl_vals)*100 if pnl_vals else 0
    print(f"{label:10} {st:>6} {nw:>5} {kl:>4} {seen:>5} {n:>4} {mean:+8.3f} {win_pct:>4.0f}%")
