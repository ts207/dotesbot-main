"""outcome_attribution.py — Strategy performance attribution and analytics.

Transforms raw closed positions into a normalized outcome format for analysis.
"""
from __future__ import annotations

import argparse
import csv
import os
from datetime import datetime, timezone
from typing import Any, Mapping

def infer_strategy_family(strategy_kind: str | None, event_type: str | None = None) -> str | None:
    """Map strategy kinds and event types to top-level strategy families."""
    kind = str(strategy_kind or "").upper()
    etype = str(event_type or "").upper()
    
    if kind == "DSWING":
        return "DSWING"
    if kind in ("VALUE_EDGE", "VALUE") or etype == "VALUE":
        return "VALUE"
    if kind in ("EVENT_CONTINUATION_EDGE", "EVENT_REVERSAL_EDGE", "EVENT_TRIGGERED_VALUE") or etype == "EVENT_TRIGGERED_VALUE":
        return "EVENT"
    if kind == "BOOK_MOVE_ALPHA":
        return "BOOK_MOVE"
    if kind == "MANUAL" or etype == "MANUAL":
        return "MANUAL"
    
    # Fallback heuristics
    if "VALUE" in kind or "VALUE" in etype: return "VALUE"
    if "EVENT" in kind or "EVENT" in etype: return "EVENT"
    
    return None

def normalize_exit_reason(reason: str | None) -> str:
    """Normalize empty or null exit reasons to 'unknown'."""
    if reason is None or str(reason).strip() == "":
        return "unknown"
    return str(reason).strip()

def closed_position_to_outcome_row(pos: dict, *, mode: str) -> dict:
    """Map raw closed position data to a flat outcome row."""
    # Ensure it's a dict
    p = dict(pos)
    
    entry_ns = p.get("entry_time_ns")
    exit_ns = p.get("exit_time_ns")
    
    # Use existing hold_sec or compute from nanosecond timestamps
    hold_sec = p.get("hold_sec")
    if hold_sec is None and entry_ns and exit_ns:
        hold_sec = (exit_ns - entry_ns) / 1e9
    
    entry_price = p.get("entry_price", 0.0)
    exit_price = p.get("exit_price", 0.0)
    shares = p.get("shares", 0.0)
    cost_usd = p.get("cost_usd", 0.0)
    
    # proceeds_usd = exit_price * shares if missing
    proceeds_usd = p.get("proceeds_usd")
    if proceeds_usd is None:
        proceeds_usd = exit_price * shares
        
    # pnl_usd = proceeds_usd - cost_usd if missing
    pnl_usd = p.get("pnl_usd")
    if pnl_usd is None:
        pnl_usd = proceeds_usd - cost_usd
        
    # roi = pnl_usd / cost_usd if cost_usd > 0 else 0.0
    roi = p.get("roi")
    if roi is None:
        roi = pnl_usd / cost_usd if cost_usd > 0 else 0.0

    strategy_kind = p.get("strategy_kind")
    strategy_family = p.get("strategy_family")
    if not strategy_family:
        strategy_family = infer_strategy_family(strategy_kind, p.get("event_type"))

    # Flatten derived state flags to string for CSV
    flags = p.get("entry_derived_state_flags", [])
    if isinstance(flags, (list, set, tuple)):
        flags_str = ",".join(sorted(str(f) for f in flags))
    else:
        flags_str = str(flags or "")

    row = {
        "position_id": p.get("position_id") or f"{p.get('match_id')}:{p.get('token_id')}:{entry_ns}",
        "mode": mode.upper(),
        "token_id": str(p.get("token_id") or ""),
        "match_id": str(p.get("match_id") or ""),
        "market_name": p.get("market_name"),
        "side": p.get("side"),

        "strategy_family": strategy_family,
        "strategy_kind": strategy_kind,
        "strategy_subtype": p.get("strategy_subtype"),
        "entry_engine": p.get("entry_engine"),
        "exit_engine": p.get("exit_engine"),
        "hold_policy": p.get("hold_policy"),
        "edge_type": p.get("edge_type"),
        "target_horizon": p.get("target_horizon"),
        "expected_hold_sec": p.get("expected_hold_sec"),
        "entry_trigger": p.get("entry_trigger"),
        "exit_trigger": p.get("exit_trigger"),
        "primary_metric": p.get("primary_metric"),
        "secondary_metric": p.get("secondary_metric"),
        "promotion_rule": p.get("promotion_rule"),
        "disable_rule": p.get("disable_rule"),

        "signal_id": p.get("signal_id"),
        "entry_time_ns": entry_ns,
        "exit_time_ns": exit_ns,
        "hold_sec": hold_sec,
        "entry_game_time_sec": p.get("entry_game_time_sec"),
        "exit_game_time_sec": p.get("exit_game_time_sec"),
        "exit_reason": normalize_exit_reason(p.get("exit_reason")),

        "entry_price": entry_price,
        "exit_price": exit_price,
        "shares": shares,
        "cost_usd": cost_usd,
        "proceeds_usd": proceeds_usd,
        "pnl_usd": pnl_usd,
        "roi": roi,

        "entry_fair": p.get("entry_fair"),
        "entry_edge": p.get("entry_edge"),
        "entry_ask": p.get("entry_ask"),
        "entry_backed_side": p.get("entry_backed_side"),
        "entry_radiant_lead": p.get("entry_radiant_lead"),
        "entry_actual_event_type": p.get("entry_actual_event_type"),
        "entry_derived_state_flags": flags_str,

        "paper_mode": p.get("paper_mode"),
        "would_pass_live": p.get("would_pass_live"),
        "live_skip_reason": p.get("live_skip_reason"),
        "paper_only_bypass": p.get("paper_only_bypass"),
        "policy_allowed": p.get("policy_allowed"),
        "policy_reason": p.get("policy_reason"),
        "policy_version": p.get("policy_version"),
        "risk_tags": p.get("risk_tags"),

        "entry_p_game": p.get("entry_p_game"),
        "entry_series_fair": p.get("entry_series_fair"),
        "entry_series_score_yes": p.get("entry_series_score_yes"),
        "entry_series_score_no": p.get("entry_series_score_no"),
        "entry_current_game_number": p.get("entry_current_game_number"),
        "entry_market_type": p.get("entry_market_type"),
        "entry_book_age_ms": p.get("entry_book_age_ms"),
    }
    return row

def load_strategy_outcomes(storage, *, modes=("paper", "live")) -> list[dict]:
    """Load and normalize outcomes from all specified modes."""
    all_rows = []
    for mode in modes:
        try:
            closed = storage.load_closed_positions(mode=mode)
            for pos in closed:
                all_rows.append(closed_position_to_outcome_row(pos, mode=mode))
        except Exception as e:
            print(f"Warning: failed to load {mode} closed positions: {e}")
    return all_rows

def write_strategy_outcomes_csv(rows: list[dict], path: str = "logs/strategy_outcomes.csv") -> None:
    """Write normalized outcome rows to a CSV file."""
    if not rows:
        return
    
    # Use the keys of the first row as headers, ensures consistency if schema is stable
    fieldnames = list(rows[0].keys())
    
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

def summarize_strategy_outcomes(rows: list[dict], *, group_by=("mode", "strategy_family", "strategy_kind")) -> list[dict]:
    """Group and aggregate outcome rows into a summary report."""
    if not rows:
        return []

    groups: dict[tuple, dict] = {}
    
    for r in rows:
        key = tuple(r.get(f) for f in group_by)
        if key not in groups:
            g = {f: r.get(f) for f in group_by}
            g.update({
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "total_cost_usd": 0.0,
                "total_pnl_usd": 0.0,
                "sum_roi": 0.0,
                "sum_hold_sec": 0.0,
                "sum_entry_edge": 0.0,
                "sum_entry_ask": 0.0,
                "sum_entry_fair": 0.0,
                "count_with_edge": 0,
                "count_with_ask": 0,
                "count_with_fair": 0,
            })
            groups[key] = g
        
        g = groups[key]
        pnl = r.get("pnl_usd", 0.0)
        g["trades"] += 1
        if pnl > 0.0001: g["wins"] += 1
        elif pnl < -0.0001: g["losses"] += 1
        
        g["total_cost_usd"] += float(r.get("cost_usd") or 0.0)
        g["total_pnl_usd"] += pnl
        g["sum_roi"] += float(r.get("roi") or 0.0)
        g["sum_hold_sec"] += float(r.get("hold_sec") or 0.0)
        
        edge = r.get("entry_edge")
        if edge is not None:
            g["sum_entry_edge"] += float(edge)
            g["count_with_edge"] += 1
            
        ask = r.get("entry_ask")
        if ask is not None:
            g["sum_entry_ask"] += float(ask)
            g["count_with_ask"] += 1
            
        fair = r.get("entry_fair")
        if fair is not None:
            g["sum_entry_fair"] += float(fair)
            g["count_with_fair"] += 1

    summary_list = []
    for g in groups.values():
        n = g["trades"]
        g["win_rate"] = g["wins"] / n if n > 0 else 0.0
        g["avg_pnl_usd"] = g["total_pnl_usd"] / n if n > 0 else 0.0
        g["avg_roi"] = g["sum_roi"] / n if n > 0 else 0.0
        g["avg_hold_sec"] = g["sum_hold_sec"] / n if n > 0 else 0.0
        g["avg_entry_edge"] = g["sum_entry_edge"] / g["count_with_edge"] if g["count_with_edge"] > 0 else 0.0
        g["avg_entry_ask"] = g["sum_entry_ask"] / g["count_with_ask"] if g["count_with_ask"] > 0 else 0.0
        g["avg_entry_fair"] = g["sum_entry_fair"] / g["count_with_fair"] if g["count_with_fair"] > 0 else 0.0
        g["pnl_per_dollar"] = g["total_pnl_usd"] / g["total_cost_usd"] if g["total_cost_usd"] > 0 else 0.0
        
        # Clean up internal sum fields
        for k in list(g.keys()):
            if k.startswith("sum_") or k.startswith("count_"):
                del g[k]
        summary_list.append(g)
        
    # Sort for deterministic output: mode, family, kind
    summary_list.sort(key=lambda x: (str(x.get("mode")), str(x.get("strategy_family")), str(x.get("strategy_kind"))))
    return summary_list

def main():
    parser = argparse.ArgumentParser(description="Strategy Outcome Attribution Report")
    parser.add_argument("--mode", choices=["paper", "live", "all"], default="all", help="Attribution mode")
    parser.add_argument("--out", default="logs/strategy_outcomes.csv", help="CSV output path")
    parser.add_argument("--summary", action="store_true", help="Print summary table to console")
    parser.add_argument("--db", help="Path to state_v2.sqlite")
    args = parser.parse_args()

    from storage_v2 import StorageV2
    storage = StorageV2(path=args.db) if args.db else StorageV2()
    
    modes = ("paper", "live") if args.mode == "all" else (args.mode,)
    rows = load_strategy_outcomes(storage, modes=modes)
    
    if not rows:
        print(f"No closed positions found for mode(s): {modes}")
        return

    write_strategy_outcomes_csv(rows, args.out)
    print(f"Exported {len(rows)} outcome rows to {args.out}")

    if args.summary:
        summary = summarize_strategy_outcomes(rows)
        print("\n" + "="*95)
        print(f"{'MODE':<8} {'FAMILY':<12} {'KIND':<20} {'TRADES':<8} {'WIN%':<8} {'TOTAL PNL':<12} {'AVG ROI':<8}")
        print("-" * 95)
        for s in summary:
            print(f"{str(s['mode']):<8} {str(s['strategy_family']):<12} {str(s['strategy_kind']):<20} "
                  f"{s['trades']:<8} {s['win_rate']:<8.3f} {s['total_pnl_usd']:<+12.2f} {s['avg_roi']:<8.3f}")
        print("="*95 + "\n")

if __name__ == "__main__":
    main()
