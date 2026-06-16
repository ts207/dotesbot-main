#!/usr/bin/env python3
"""
Run the survival-feature marginal value audit.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.analyze_value_survival_features import build_report, TRADES_PATH, RAW_PATHS

def _to_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0

def summarize_group(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    n = len(rows)
    wins = sum(1 for r in rows if str(r.get("won")) == "1")
    losses = n - wins
    
    pnls = [_to_float(r.get("pnl_usd")) for r in rows]
    stakes = [_to_float(r.get("stake_usd")) for r in rows]
    edges = [_to_float(r.get("edge")) for r in rows]
    fairs = [_to_float(r.get("fair")) for r in rows]
    asks = [_to_float(r.get("entry_price")) for r in rows]
    book_ages = [_to_float(r.get("book_age_ms")) for r in rows]
    game_times = [_to_float(r.get("game_time_sec")) for r in rows]
    
    settlement_pnl = sum(pnls)
    total_stake = sum(stakes)
    roi = settlement_pnl / total_stake if total_stake > 0 else 0.0
    
    avg_edge = sum(edges) / n if n > 0 else 0.0
    avg_fair = sum(fairs) / n if n > 0 else 0.0
    avg_ask = sum(asks) / n if n > 0 else 0.0
    avg_book_age_ms = sum(book_ages) / n if n > 0 else 0.0
    avg_game_time_sec = sum(game_times) / n if n > 0 else 0.0
    
    sorted_pnls = sorted(pnls, reverse=True)
    positive_pnl = sum(p for p in pnls if p > 0)
    # the prompt asked for "top_1_trade_share" -> proportion of total settlement_pnl or positive_pnl?
    # typically top trades share is sum of top K / positive_pnl or total_pnl.
    # We will use positive_pnl to avoid > 100% when total pnl is small, or just sum of top K / total PNL if total PNL > 0.
    denom = settlement_pnl if settlement_pnl > 0 else 1.0
    
    top_1_trade_share = sum(sorted_pnls[:1]) / denom if n > 0 and denom > 0 else 0.0
    top_3_trade_share = sum(sorted_pnls[:3]) / denom if n > 0 and denom > 0 else 0.0
    
    return {
        "group": name,
        "trades": n,
        "wins": wins,
        "losses": losses,
        "settlement_pnl": settlement_pnl,
        "ROI": roi,
        "avg_edge": avg_edge,
        "avg_fair": avg_fair,
        "avg_ask": avg_ask,
        "avg_book_age_ms": avg_book_age_ms,
        "avg_game_time_sec": avg_game_time_sec,
        "top_1_trade_share": top_1_trade_share,
        "top_3_trade_share": top_3_trade_share,
    }

def print_markdown(stats: dict[str, Any]):
    print(f"### {stats['group']}")
    print(f"- trades: {stats['trades']}")
    print(f"- wins: {stats['wins']}")
    print(f"- losses: {stats['losses']}")
    print(f"- settlement_pnl: ${stats['settlement_pnl']:.2f}")
    print(f"- ROI: {stats['ROI']*100:.1f}%")
    print(f"- avg_edge: {stats['avg_edge']:.3f}")
    print(f"- avg_fair: {stats['avg_fair']:.3f}")
    print(f"- avg_ask: {stats['avg_ask']:.3f}")
    print(f"- avg_book_age_ms: {stats['avg_book_age_ms']:.1f}")
    print(f"- avg_game_time_sec: {stats['avg_game_time_sec']:.1f}")
    print(f"- top_1_trade_share: {stats['top_1_trade_share']*100:.1f}%")
    print(f"- top_3_trade_share: {stats['top_3_trade_share']*100:.1f}%")
    print()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trades", type=Path, default=TRADES_PATH)
    parser.add_argument("--raw", type=Path, action="append", default=None)
    args = parser.parse_args()

    raw_paths = args.raw if args.raw else RAW_PATHS
    report, enriched = build_report(args.trades, raw_paths)

    baseline_v1_all = enriched
    
    baseline_v1_joined = [r for r in enriched if r.get("snapshot_joined")]
    
    score_tower_aligned = []
    for r in enriched:
        if not r.get("snapshot_joined"):
            continue
        kill_diff = r.get("leader_kill_diff")
        tower_diff = r.get("leader_tower_diff")
        if kill_diff is not None and tower_diff is not None and kill_diff >= 0 and tower_diff >= 0:
            score_tower_aligned.append(r)
            
    aligned_ids = {r.get("match_id") for r in score_tower_aligned}
    score_tower_not_aligned_or_missing = [r for r in enriched if r.get("match_id") not in aligned_ids]
    
    missing_toplive_context = [r for r in enriched if not r.get("snapshot_joined")]
    
    groups = [
        ("baseline_v1_all_18", baseline_v1_all),
        ("baseline_v1_joined_15", baseline_v1_joined),
        ("score_tower_aligned_14", score_tower_aligned),
        ("score_tower_not_aligned_or_missing_4", score_tower_not_aligned_or_missing),
        ("missing_toplive_context_3", missing_toplive_context),
    ]
    
    print("## Survival-Feature Marginal Value Audit")
    print()
    for name, rows in groups:
        stats = summarize_group(rows, name)
        print_markdown(stats)

if __name__ == "__main__":
    main()
