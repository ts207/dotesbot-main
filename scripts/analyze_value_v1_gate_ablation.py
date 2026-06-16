#!/usr/bin/env python3
"""
Gate ablation audit for Value Bot v1.
Systematically tests variations of non-edge risk gates (min_time, max_time, book_age, max_price, min_fair)
against the baseline policy to measure marginal contribution and expectancy.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import sys
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.backtest_value_engine import (
    book_at,
    fair_price,
    load_books,
    load_markets,
    load_outcomes,
    load_snapshots,
    resolve_yes_won,
    signal_side,
    _params,
)
from unified_storage.event_store import load_manual_windows, manual_window_reason

CONFIG_PATH = REPO_ROOT / "configs" / "gettoplive_policy_comparison_v1.yaml"

def load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)

def top_share(values: list[float], n: int) -> float | None:
    if not values:
        return None
    denom = sum(abs(v) for v in values)
    if denom == 0:
        return None
    return sum(abs(v) for v in sorted(values, key=lambda x: abs(x), reverse=True)[:n]) / denom

def calculate_max_drawdown(pnls: list[float]) -> float:
    peak = 0.0
    current = 0.0
    max_dd = 0.0
    for p in pnls:
        current += p
        if current > peak:
            peak = current
        dd = peak - current
        if dd > max_dd:
            max_dd = dd
    return max_dd

def generate_candidates(baseline_params: dict) -> list[dict]:
    cands = []
    
    # 1. Baseline
    cands.append({
        "id": "baseline_v1",
        "changed_gate": "none",
        "gate_value": "baseline",
        "params": dict(baseline_params)
    })
    
    # 2. min_time ablations
    min_time_vals = [("no_min_time_gate", 0), ("min_time_480s", 480), ("min_time_540s", 540), ("min_time_720s", 720)]
    for name, val in min_time_vals:
        p = dict(baseline_params)
        p["min_time"] = val
        cands.append({"id": name, "changed_gate": "min_time", "gate_value": val, "params": p})
        
    # 3. max_time ablations
    max_time_vals = [("no_max_time_gate", 9999999), ("max_time_2100s", 2100), ("max_time_2700s", 2700)]
    for name, val in max_time_vals:
        p = dict(baseline_params)
        p["max_time"] = val
        cands.append({"id": name, "changed_gate": "max_time", "gate_value": val, "params": p})
        
    # 4. book_age ablations
    book_age_vals = [("book_age_5s", 5000), ("book_age_10s", 10000), ("book_age_30s", 30000), ("no_book_age_gate", 999999999)]
    for name, val in book_age_vals:
        p = dict(baseline_params)
        p["book_age_ms"] = val
        cands.append({"id": name, "changed_gate": "book_age_ms", "gate_value": val, "params": p})
        
    # 5. max_price ablations
    max_price_vals = [("max_price_0.80", 0.80), ("max_price_0.88", 0.88), ("max_price_0.92", 0.92), ("no_price_ceiling", 1.0)]
    for name, val in max_price_vals:
        p = dict(baseline_params)
        p["max_price"] = val
        cands.append({"id": name, "changed_gate": "max_price", "gate_value": val, "params": p})
        
    # 6. min_fair ablations
    min_fair_vals = [("min_fair_0.65", 0.65), ("min_fair_0.75", 0.75)]
    for name, val in min_fair_vals:
        p = dict(baseline_params)
        p["min_fair"] = val
        cands.append({"id": name, "changed_gate": "min_fair", "gate_value": val, "params": p})
        
    # 7. no price floor
    p = dict(baseline_params)
    p["min_price"] = 0.0
    cands.append({"id": "no_price_floor", "changed_gate": "min_price", "gate_value": 0.0, "params": p})
    
    return cands

def main() -> int:
    config = load_config()
    base_params = _params()
    
    outcomes, outcome_sources = load_outcomes()
    markets, skipped = load_markets()
    snapshots = load_snapshots(set(markets))
    
    tokens: set[str] = set()
    for match_id in snapshots:
        tokens.add(str(markets[match_id]["yes_token_id"]))
        tokens.add(str(markets[match_id]["no_token_id"]))
    book = load_books(tokens)
    
    joined = {
        match_id: rows
        for match_id, rows in snapshots.items()
        if str(markets[match_id]["yes_token_id"]) in book
        and str(markets[match_id]["no_token_id"]) in book
    }
    
    manual_windows = []
    if config.get("manual_windows_excluded"):
        manual_windows = load_manual_windows(REPO_ROOT / "data" / "manual" / "excluded_time_windows.csv")
        
    candidates = generate_candidates(base_params)
    
    trades_per_candidate = defaultdict(list)
    causality_violations = Counter()
    
    print(f"Running gate ablation audit on {len(candidates)} candidates across {len(joined)} joined matches...")
    
    for match_id, rows in joined.items():
        mapping = markets[match_id]
        yes_token = str(mapping["yes_token_id"])
        no_token = str(mapping["no_token_id"])
        
        yes_won, source = resolve_yes_won(match_id, mapping, book, outcomes, outcome_sources)
        if yes_won is None:
            continue
            
        history = deque(maxlen=4000)
        value_bot_history = deque(maxlen=4000)
        entered_per_candidate = {c["id"]: False for c in candidates}
        
        for row in rows:
            if all(entered_per_candidate.values()):
                break
                
            ns = int(row.get("received_at_ns") or 0)
            game_time = row.get("game_time_sec")
            lead = row.get("radiant_lead")
            
            if row.get("game_over") or game_time is None or lead is None:
                continue
                
            lead = int(lead)
            history.append((ns, lead, game_time))
            
            side, direction = signal_side(mapping, lead)
            token = yes_token if side == "YES" else no_token if side == "NO" else None
            entry_book = book_at(book, token, ns) if token else None
            
            ask = None
            book_age_ms = 0
            if entry_book:
                book_age_ms = (ns - int(entry_book["received_at_ns"])) / 1_000_000
                a = entry_book.get("best_ask")
                if a is not None and not math.isnan(float(a)):
                    ask = float(a)
                    
            manual_reason = manual_window_reason(ns, manual_windows)
            causality_valid = ns >= int(entry_book["received_at_ns"]) if entry_book else False
            
            for cand in candidates:
                cid = cand["id"]
                if entered_per_candidate[cid]:
                    continue
                    
                p = cand["params"]
                
                # Apply gates
                if game_time < p["min_time"]: continue
                if game_time > p["max_time"]: continue
                if abs(lead) < p["min_lead"]: continue
                if side is None: continue
                if not entry_book: continue
                if book_age_ms > p["book_age_ms"]: continue
                if ask is None: continue
                if ask > p["max_price"]: continue
                if ask < p["min_price"]: continue
                if abs(lead) > p["flip_lead"] and ask < p["flip_ask_floor"]: continue
                if manual_reason: continue
                
                if not causality_valid:
                    causality_violations[cid] += 1
                    continue
                    
                cand_history = deque((h_ns, h_lead) for h_ns, h_lead, h_gt in history if h_gt >= p["min_time"] and h_gt <= p["max_time"])
                fair = fair_price(row, direction, lead, cand_history)
                edge = fair - ask
                
                if fair < p["min_fair"]: continue
                if edge < p["min_edge"]: continue
                if edge > p["max_edge"]: continue
                
                entered_per_candidate[cid] = True
                
                won = 1 if (yes_won == 1 and side == "YES") or (yes_won == 0 and side == "NO") else 0
                stake = p["stake"]
                payout = stake / ask if won else 0
                settlement_pnl = payout - stake
                
                trades_per_candidate[cid].append({
                    "match_id": match_id,
                    "poll_ts": ns,
                    "entry_ask": ask,
                    "edge": edge,
                    "fair": fair,
                    "book_age_ms": book_age_ms,
                    "won": won,
                    "stake_usd": stake,
                    "settlement_pnl": settlement_pnl
                })

    if sum(causality_violations.values()) > 0:
        print("CRITICAL ERROR: Causality violations detected. Halting.")
        print(causality_violations)
        sys.exit(1)

    print("Generating ablation reports...")
    reports_dir = REPO_ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    
    baseline_trades = trades_per_candidate["baseline_v1"]
    expected_trades = config["value_reconciliation"]["expected_trades"]
    expected_pnl = config["value_reconciliation"]["expected_pnl_usd"]
    
    b_trades = len(baseline_trades)
    b_pnl = sum(t["settlement_pnl"] for t in baseline_trades)
    baseline_reconciliation_pass = (b_trades == expected_trades and abs(b_pnl - expected_pnl) < 1.0)
    
    if not baseline_reconciliation_pass:
        print(f"WARNING: Baseline reconciliation failed. Expected {expected_trades} trades / ${expected_pnl}. Observed {b_trades} / ${b_pnl}")
        # Note: We changed fair_price to use full history instead of bounded value_bot_history 
        # so this might deviate slightly from exact $83.58. We will see.
        print("Continuing generation for diagnostic purposes...")
        
    baseline_trade_ids = {(t["match_id"], t["poll_ts"]) for t in baseline_trades}

    results = []
    for cand in candidates:
        cid = cand["id"]
        trades = trades_per_candidate[cid]
        fills = len(trades)
        
        if fills == 0:
            results.append({
                "candidate_id": cid,
                "changed_gate": cand["changed_gate"],
                "gate_value": cand["gate_value"],
                "fills": 0
            })
            continue
            
        settlement_pnl_sum = sum(t["settlement_pnl"] for t in trades)
        wins = sum(1 for t in trades if t["settlement_pnl"] > 0)
        win_rate = wins / fills
        stake_sum = sum(t["stake_usd"] for t in trades)
        roi = settlement_pnl_sum / stake_sum if stake_sum > 0 else 0
        
        pnls = [t["settlement_pnl"] for t in trades]
        t1 = top_share(pnls, 1) or 0.0
        t3 = top_share(pnls, 3) or 0.0
        t5 = top_share(pnls, 5) or 0.0
        
        max_dd = calculate_max_drawdown(pnls)
        
        avg_ask = sum(t["entry_ask"] for t in trades) / fills
        avg_edge = sum(t["edge"] for t in trades) / fills
        avg_fair = sum(t["fair"] for t in trades) / fills
        avg_book_age = sum(t["book_age_ms"] for t in trades) / fills
        
        cand_trade_ids = {(t["match_id"], t["poll_ts"]) for t in trades}
        new_trade_ids = cand_trade_ids - baseline_trade_ids
        dropped_trade_ids = baseline_trade_ids - cand_trade_ids
        
        marginal_trades = [t for t in trades if (t["match_id"], t["poll_ts"]) in new_trade_ids]
        marginal_pnl = sum(t["settlement_pnl"] for t in marginal_trades)
        marginal_wins = sum(1 for t in marginal_trades if t["settlement_pnl"] > 0)
        marginal_win_rate = marginal_wins / len(marginal_trades) if marginal_trades else None
        
        warning = ""
        if fills < 20: warning = "LOW_SAMPLE_SIZE"
        elif t5 > 0.60: warning = "HIGH_CONCENTRATION"
        
        results.append({
            "candidate_id": cid,
            "changed_gate": cand["changed_gate"],
            "gate_value": cand["gate_value"],
            "fills": fills,
            "unique_matches": len(set(t["match_id"] for t in trades)),
            "settlement_pnl": settlement_pnl_sum,
            "ROI": roi,
            "win_rate": win_rate,
            "max_drawdown": max_dd,
            "avg_entry_ask": avg_ask,
            "avg_edge": avg_edge,
            "avg_fair": avg_fair,
            "avg_book_age_ms": avg_book_age,
            "top_1_trade_share": t1,
            "top_3_trade_share": t3,
            "top_5_trade_share": t5,
            "new_trades_vs_baseline": len(new_trade_ids),
            "dropped_baseline_trades": len(dropped_trade_ids),
            "marginal_trade_pnl": marginal_pnl,
            "marginal_trade_win_rate": marginal_win_rate,
            "sample_size_warning": warning,
            "concentration_warning": warning,
            "causality_violations": causality_violations[cid],
            "baseline_reconciliation_pass": baseline_reconciliation_pass
        })
        
    with open(reports_dir / "value_v1_gate_ablation.csv", "w", newline="") as f:
        headers = [k for k in results[0].keys()]
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(results)
        
    print(f"Generated ablation audit. Results written to {reports_dir / 'value_v1_gate_ablation.csv'}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
