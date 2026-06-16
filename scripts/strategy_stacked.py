#!/usr/bin/env python3
"""Stack the value-positive findings from strategy_investigation.py and
quantify the cumulative lift.

Stacked changes (in order of application):
    A. Baseline: POLL_FIGHT_SWING + ref-band [0.30, 0.85], 30s hold, flat $5 sizing
    B. + Skip spread band [0.02, 0.05)
    C. + Drop phases {<15m, 45-50m, >60m}
    D. + Differential hold (60s for close_game, 30s for the rest)
    E. + Edge-weighted sizing (1.5× small NW, 1.0× medium, 0.5× big)
    F. + Long hold for lead_extension (120s)
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Re-use the builder + classifier from the investigation script.
from strategy_investigation import build_ledger, classify, NOTIONAL, COST_HALF_SPREAD


def hold_for(t, mode: str):
    """Return the hold-horizon to use given the mode."""
    if mode == "flat30":
        return 30
    if mode == "differential":
        c = classify(t)
        if c == "close_game":
            return 60
        if c == "comeback_against":
            return 30
        return 30  # lead_extension default
    if mode == "differential_lead_extends_to_120":
        c = classify(t)
        if c == "close_game":
            return 60
        if c == "comeback_against":
            return 30
        return 120  # lead_extension long-hold
    raise ValueError(mode)


def pnl_for(t, mode: str, sizing: str):
    horizon = hold_for(t, mode)
    dm = t["markouts"].get(horizon)
    if dm is None:
        return None
    base = ((dm * t["direction"] - COST_HALF_SPREAD) / t["ref"]) * NOTIONAL
    if sizing == "flat":
        return base
    if sizing == "edge_weighted":
        dl = abs(t["d_lead_raw"])
        mult = 1.5 if dl < 2800 else (1.0 if dl < 5000 else 0.5)
        return base * mult
    raise ValueError(sizing)


def keep(t, drop_spread_trap: bool, drop_weak_phases: bool) -> bool:
    if drop_spread_trap and t["spread"] is not None and 0.02 <= t["spread"] < 0.05:
        return False
    if drop_weak_phases:
        gt = t["gt"]
        if gt < 900:                 return False
        if 2700 <= gt < 3000:        return False
        if gt >= 3600:               return False
    return True


def stats(pnls, label=""):
    if not pnls:
        return f"{label}: 0"
    pnls = sorted(pnls)
    n = len(pnls)
    w = sum(1 for p in pnls if p > 0)
    return (f"n={n:>4} total=${sum(pnls):+7.2f} ${sum(pnls)/n:+.3f}/t "
            f"win={100*w/n:>3.0f}% med=${pnls[n//2]:+.3f} p25=${pnls[n//4]:+.3f} p75=${pnls[3*n//4]:+.3f}")


def run(trades, *, label, drop_spread_trap, drop_weak_phases, mode, sizing):
    pnls = []
    for t in trades:
        if not keep(t, drop_spread_trap, drop_weak_phases):
            continue
        p = pnl_for(t, mode, sizing)
        if p is not None:
            pnls.append(p)
    return label, pnls


def main():
    trades, _ = build_ledger()
    print(f"Loaded {len(trades)} candidate trades on {len(set(t['mid'] for t in trades))} matches\n")
    print("="*98)
    print("Stacked strategy improvements (each row adds one change on top of all preceding rows)")
    print("="*98)
    runs = [
        run(trades, label="A. Baseline (30s flat $5)",
            drop_spread_trap=False, drop_weak_phases=False, mode="flat30", sizing="flat"),
        run(trades, label="B. + Skip spread [0.02, 0.05)",
            drop_spread_trap=True,  drop_weak_phases=False, mode="flat30", sizing="flat"),
        run(trades, label="C. + Drop <15m, 45-50m, >60m",
            drop_spread_trap=True,  drop_weak_phases=True,  mode="flat30", sizing="flat"),
        run(trades, label="D. + Differential hold (close=60s)",
            drop_spread_trap=True,  drop_weak_phases=True,  mode="differential", sizing="flat"),
        run(trades, label="E. + Edge-weighted sizing",
            drop_spread_trap=True,  drop_weak_phases=True,  mode="differential", sizing="edge_weighted"),
        run(trades, label="F. + Lead-extension 120s hold",
            drop_spread_trap=True,  drop_weak_phases=True,  mode="differential_lead_extends_to_120", sizing="edge_weighted"),
    ]
    for label, pnls in runs:
        print(f"  {label:<48} {stats(pnls)}")
    print()
    print("Each row's lift vs. baseline:")
    base_total = sum(runs[0][1])
    base_pt    = base_total / len(runs[0][1])
    for label, pnls in runs[1:]:
        total = sum(pnls)
        pt    = total / len(pnls) if pnls else 0
        print(f"  {label:<48} Δtotal={total - base_total:+6.2f}  Δ$/t={pt - base_pt:+.4f}  Δn={len(pnls) - len(runs[0][1]):+d}")

    # Also drawdown
    print("\nDrawdown check (chronological by match_id + ns):")
    for label, pnls in runs[::len(runs)-1]:  # baseline and final
        ordered = sorted([(t["mid"], t["ns"], pnl_for(t, "differential_lead_extends_to_120" if "F." in label else "flat30",
                                                       "edge_weighted" if "F." in label else "flat"))
                          for t in trades
                          if keep(t, "+ Skip" in label or "+ " in label, "Drop" in label or "+ " in label)])
        ordered = [p for _,_,p in ordered if p is not None]
        cum, peak, mdd = 0.0, 0.0, 0.0
        for p in ordered:
            cum += p
            peak = max(peak, cum)
            mdd = max(mdd, peak - cum)
        print(f"  {label:<48} total=${cum:+.2f}  max_dd=${mdd:.2f}")


if __name__ == "__main__":
    main()
