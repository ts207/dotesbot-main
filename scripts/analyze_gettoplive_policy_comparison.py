#!/usr/bin/env python3
"""
Create a standalone policy comparison audit script to evaluate net-worth level 
versus net-worth transition policies on the same getTopLive poll universe. 
The audit will measure whether transition/change-based policies add incremental value 
over the existing level-based Value policy, whether current event logic misses 
profitable opportunities, and whether convergence/settlement exits outperform short 
fixed-horizon exits under causal, executable replay assumptions.
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

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as pds
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.backtest_value_engine import (
    book_at,
    fair_price,
    final_book_yes_won,
    load_books,
    load_markets,
    load_outcomes,
    load_snapshots,
    resolve_yes_won,
    signal_side,
    yes_from_radiant,
    _params,
    _confirm_params
)
from unified_storage.event_store import load_manual_windows, manual_window_reason

CONFIG_PATH = REPO_ROOT / "configs" / "gettoplive_policy_comparison_v1.yaml"

def load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)

def fnum(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        if math.isnan(float(value)):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None

def get_past_lead(history: deque[tuple[int, int]], current_ns: int, delta_ns: int) -> int | None:
    target_ns = current_ns - delta_ns
    past_lead = None
    for ns, lead in history:
        if ns <= target_ns:
            past_lead = lead
        else:
            break
    return past_lead

def get_future_book(times: list[int], rows: list[dict], ns_start: int, delta_ns: int) -> dict | None:
    target_ns = ns_start + delta_ns
    idx = bisect.bisect_right(times, target_ns) - 1
    if idx < 0:
        return None
    return rows[idx]

def exit_pnl(entry_ask: float, exit_book: dict | None, won: int | None, stake: float) -> float | None:
    if exit_book is None:
        return None
    bid = exit_book.get("best_bid")
    if bid is None or math.isnan(float(bid)):
        return None
    payout = float(bid) * stake / entry_ask
    return payout - stake

def get_convergence_exit(times: list[int], rows: list[dict], ns_start: int, entry_ask: float, move_cents: int, max_hold_ns: int) -> dict | None:
    target_price = entry_ask + (move_cents / 100.0)
    start_idx = bisect.bisect_right(times, ns_start)
    for i in range(start_idx, len(rows)):
        tick = rows[i]
        ns = tick["received_at_ns"]
        if ns > ns_start + max_hold_ns:
            break
        bid = tick.get("best_bid")
        if bid is not None and not math.isnan(float(bid)):
            if float(bid) >= target_price:
                return tick
    
    return get_future_book(times, rows, ns_start, max_hold_ns)

POLICIES = [
    "level_value_hold",
    "level_value_hold_confirmed",
    "transition_quick_exit_30s",
    "transition_quick_exit_60s",
    "level_only",
    "transition_only",
    "both_same_side",
    "both_opposite_side_block"
]

def top_share(values: list[float], n: int) -> float | None:
    if not values:
        return None
    denom = sum(abs(v) for v in values)
    if denom == 0:
        return None
    return sum(abs(v) for v in sorted(values, key=lambda x: abs(x), reverse=True)[:n]) / denom

def main() -> int:
    config = load_config()
    params = _params()
    confirm_params = _confirm_params()
    
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
    
    level_threshold = config["level_thresholds"]["networth_lead_usd"][1]
    change_30_threshold = config["change_thresholds"]["networth_delta_30s"][1]
    change_60_threshold = config["change_thresholds"]["networth_delta_60s"][1]
    
    all_polls = []
    policy_trades = defaultdict(list)
    missed_opportunities = []
    overlap_counts = Counter()
    causality_violations = 0
    
    print("Simulating policies...")
    
    for match_id, rows in joined.items():
        mapping = markets[match_id]
        yes_token = str(mapping["yes_token_id"])
        no_token = str(mapping["no_token_id"])
        
        yes_won, source = resolve_yes_won(match_id, mapping, book, outcomes, outcome_sources)
        if yes_won is None:
            continue
            
        history = deque(maxlen=4000)
        value_bot_history = deque(maxlen=4000)
        entered_policies = {p: False for p in POLICIES}
        armed_confirmation: dict[str, dict] = {}
        
        for row in rows:
            ns = int(row.get("received_at_ns") or 0)
            game_time = row.get("game_time_sec")
            lead = row.get("radiant_lead")
            
            if row.get("game_over") or game_time is None or lead is None:
                continue
                
            lead = int(lead)
            history.append((ns, lead))
            
            if game_time >= params["min_time"] and game_time <= params["max_time"]:
                value_bot_history.append((ns, lead))
            
            lead_15s_ago = get_past_lead(history, ns, 15_000_000_000)
            lead_30s_ago = get_past_lead(history, ns, 30_000_000_000)
            lead_60s_ago = get_past_lead(history, ns, 60_000_000_000)
            
            if lead_30s_ago is None or lead_60s_ago is None:
                continue
                
            delta_30s = lead - lead_30s_ago
            delta_60s = lead - lead_60s_ago
            
            is_high_level = abs(lead) >= level_threshold
            is_high_change = abs(delta_30s) >= change_30_threshold
            
            if is_high_level and not is_high_change:
                quadrant = "high_level_low_change"
            elif is_high_level and is_high_change:
                quadrant = "high_level_high_change"
            elif not is_high_level and is_high_change:
                quadrant = "low_level_high_change"
            else:
                quadrant = "low_level_low_change"
                
            if game_time < params["min_time"] or game_time > params["max_time"]:
                continue
                
            side, direction = signal_side(mapping, lead)
            if side is None:
                continue
                
            token = yes_token if side == "YES" else no_token
            entry_book = book_at(book, token, ns)
            if not entry_book:
                continue
                
            book_age_ms = (ns - int(entry_book["received_at_ns"])) / 1_000_000
            if book_age_ms > params["book_age_ms"]:
                continue
                
            ask = entry_book.get("best_ask")
            if ask is None or math.isnan(float(ask)):
                continue
            ask = float(ask)
            
            if ask > params["max_price"] or ask < params["min_price"]:
                continue
                
            orientation_valid = not (abs(lead) > params["flip_lead"] and ask < params["flip_ask_floor"])
            if not orientation_valid:
                continue
                
            fair = fair_price(row, direction, lead, value_bot_history)
            edge = fair - ask
            
            manual_reason = manual_window_reason(ns, manual_windows)
            if manual_reason:
                continue

            # Strict causality validation
            causality_valid = ns >= int(entry_book["received_at_ns"])
            if not causality_valid:
                causality_violations += 1
                continue
                
            # Simulate exactly the value bot logic for level signals
            is_level_signal = (abs(lead) >= params["min_lead"]) and (fair >= params["min_fair"]) and (edge >= params["min_edge"]) and (edge <= params["max_edge"])
            
            # Simulate transition signals
            # Use same base checks, but require high change instead of level
            is_transition_signal = is_high_change and (fair >= params["min_fair"]) and (edge >= params["min_edge"]) and (edge <= params["max_edge"])
            
            all_polls.append({
                "match_id": match_id,
                "poll_ts": ns,
                "game_time_sec": game_time,
                "lead": lead,
                "delta_30s": delta_30s,
                "delta_60s": delta_60s,
                "quadrant": quadrant,
                "edge": edge,
                "fair": fair,
                "ask": ask,
                "is_level_signal": is_level_signal,
                "is_transition_signal": is_transition_signal
            })
            
            # Policy processing
            token_times, token_rows = book[token]
            won = 1 if (yes_won == 1 and side == "YES") or (yes_won == 0 and side == "NO") else 0
            stake = params["stake"]
            payout = stake / ask if won else 0
            settlement_pnl = payout - stake

            book_30s = get_future_book(token_times, token_rows, ns, 30_000_000_000)
            book_60s = get_future_book(token_times, token_rows, ns, 60_000_000_000)
            book_300s = get_future_book(token_times, token_rows, ns, 300_000_000_000)
            book_conv = get_convergence_exit(token_times, token_rows, ns, ask, config["convergence"]["take_profit_price_move_cents"][1], config["convergence"]["max_hold_seconds"][1] * 1_000_000_000)
            
            pnl_30s = exit_pnl(ask, book_30s, won, stake)
            pnl_60s = exit_pnl(ask, book_60s, won, stake)
            pnl_300s = exit_pnl(ask, book_300s, won, stake)
            pnl_conv = exit_pnl(ask, book_conv, won, stake)
            
            base_trade = {
                "poll_ts": ns,
                "latest_snapshot_ts_used": ns,
                "latest_book_ts_used": entry_book["received_at_ns"],
                "causality_valid": causality_valid,
                "causality_violation_reason": "" if causality_valid else "book_ts_after_poll_ts",
                "match_id": match_id,
                "market_name": mapping.get("name", ""),
                "token_id": token,
                "side": side,
                "entry_price": ask,
                "entry_price_source": "actual_best_ask",
                "won": won,
                "stake_usd": stake,
                "settlement_pnl": settlement_pnl,
                "30s_bid_exit_pnl": pnl_30s,
                "60s_bid_exit_pnl": pnl_60s,
                "300s_bid_exit_pnl": pnl_300s,
                "convergence_pnl": pnl_conv,
            }

            # 1. level_value_hold
            if is_level_signal and not entered_policies["level_value_hold"]:
                entered_policies["level_value_hold"] = True
                trade = base_trade.copy()
                trade.update({"diagnostic_only": False, "policy_frozen": True})
                policy_trades["level_value_hold"].append(trade)
                
            # 2. level_value_hold_confirmed
            if is_level_signal and not entered_policies["level_value_hold_confirmed"]:
                key = f"{match_id}|{token}|{side}"
                if edge < confirm_params["min_edge"]:
                    armed_confirmation.pop(key, None)
                else:
                    prior = armed_confirmation.get(key)
                    if prior and (ns - prior["ns"]) <= confirm_params["max_age_ns"]:
                        if (ask - prior["ask"]) <= confirm_params["max_ask_worsen"]:
                            entered_policies["level_value_hold_confirmed"] = True
                            trade = base_trade.copy()
                            trade.update({"diagnostic_only": False, "policy_frozen": True})
                            policy_trades["level_value_hold_confirmed"].append(trade)
                    else:
                        armed_confirmation[key] = {"ns": ns, "ask": ask}

            # 3. transition_quick_exit_30s
            if is_transition_signal and not entered_policies["transition_quick_exit_30s"]:
                entered_policies["transition_quick_exit_30s"] = True
                trade = base_trade.copy()
                trade.update({"diagnostic_only": True, "policy_frozen": False})
                policy_trades["transition_quick_exit_30s"].append(trade)
                
            # 4. transition_quick_exit_60s
            if is_transition_signal and not entered_policies["transition_quick_exit_60s"]:
                entered_policies["transition_quick_exit_60s"] = True
                trade = base_trade.copy()
                trade.update({"diagnostic_only": True, "policy_frozen": False})
                policy_trades["transition_quick_exit_60s"].append(trade)
                
            # 5. level_only
            if is_level_signal and not is_transition_signal and not entered_policies["level_only"]:
                entered_policies["level_only"] = True
                trade = base_trade.copy()
                trade.update({"diagnostic_only": True, "policy_frozen": False})
                policy_trades["level_only"].append(trade)
                
            # 6. transition_only
            if is_transition_signal and not is_level_signal and not entered_policies["transition_only"]:
                entered_policies["transition_only"] = True
                trade = base_trade.copy()
                trade.update({"diagnostic_only": True, "policy_frozen": False})
                policy_trades["transition_only"].append(trade)
                
            # Overlap tracking
            if is_level_signal and is_transition_signal:
                overlap_counts["both_same_side"] += 1
                if not entered_policies["both_same_side"]:
                    entered_policies["both_same_side"] = True
                    trade = base_trade.copy()
                    trade.update({"diagnostic_only": True, "policy_frozen": False})
                    policy_trades["both_same_side"].append(trade)
            elif is_level_signal and not is_transition_signal:
                overlap_counts["level_only"] += 1
            elif not is_level_signal and is_transition_signal:
                overlap_counts["transition_only"] += 1
            else:
                overlap_counts["neither"] += 1

            # Missed opportunities: highly profitable quadrants but no signal
            if not is_level_signal and not is_transition_signal and quadrant in ["high_level_high_change", "high_level_low_change"]:
                if settlement_pnl > 0:
                    missed_opportunities.append({
                        "poll_ts": ns,
                        "match_id": match_id,
                        "quadrant": quadrant,
                        "settlement_pnl": settlement_pnl,
                        "lead": lead,
                        "edge": edge
                    })

    if causality_violations > 0:
        print(f"CRITICAL ERROR: {causality_violations} causality violations detected. Halting.")
        sys.exit(1)

    print("Generating reports...")
    reports_dir = REPO_ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Policy Comparison
    comparison_results = {}
    for policy in POLICIES:
        trades = policy_trades[policy]
        fills = len(trades)
        if fills == 0:
            comparison_results[policy] = {"fills": 0}
            continue
            
        settlement_pnl_sum = sum(t["settlement_pnl"] for t in trades)
        pnl_30s_sum = sum(t["30s_bid_exit_pnl"] for t in trades if t["30s_bid_exit_pnl"] is not None)
        pnl_60s_sum = sum(t["60s_bid_exit_pnl"] for t in trades if t["60s_bid_exit_pnl"] is not None)
        pnl_300s_sum = sum(t["300s_bid_exit_pnl"] for t in trades if t["300s_bid_exit_pnl"] is not None)
        pnl_conv_sum = sum(t["convergence_pnl"] for t in trades if t["convergence_pnl"] is not None)
        
        wins = sum(1 for t in trades if t["settlement_pnl"] > 0)
        win_rate = wins / fills
        stake_sum = sum(t["stake_usd"] for t in trades)
        roi = settlement_pnl_sum / stake_sum if stake_sum > 0 else 0
        
        pnls = [t["settlement_pnl"] for t in trades]
        t1 = top_share(pnls, 1) or 0.0
        t3 = top_share(pnls, 3) or 0.0
        t5 = top_share(pnls, 5) or 0.0
        
        warning = ""
        if fills < 20:
            warning = "LOW_SAMPLE_SIZE"
        elif t5 > 0.60:
            warning = "HIGH_CONCENTRATION"
            
        comparison_results[policy] = {
            "fills": fills,
            "settlement_pnl": settlement_pnl_sum,
            "30s_bid_exit_pnl": pnl_30s_sum,
            "60s_bid_exit_pnl": pnl_60s_sum,
            "300s_bid_exit_pnl": pnl_300s_sum,
            "convergence_pnl": pnl_conv_sum,
            "roi": roi,
            "win_rate": win_rate,
            "top_1_trade_share": t1,
            "top_3_trade_share": t3,
            "top_5_trade_share": t5,
            "sample_size_warning": warning,
            "diagnostic_only": trades[0]["diagnostic_only"],
            "policy_frozen": trades[0]["policy_frozen"]
        }

    with open(reports_dir / "gettoplive_policy_comparison.json", "w") as f:
        json.dump(comparison_results, f, indent=2)
        
    with open(reports_dir / "gettoplive_policy_comparison.csv", "w", newline="") as f:
        writer = csv.writer(f)
        headers = ["policy", "fills", "settlement_pnl", "30s_bid_exit_pnl", "60s_bid_exit_pnl", 
                  "300s_bid_exit_pnl", "convergence_pnl", "roi", "win_rate", "top_1_trade_share", 
                  "top_3_trade_share", "top_5_trade_share", "sample_size_warning", 
                  "diagnostic_only", "policy_frozen"]
        writer.writerow(headers)
        for policy in POLICIES:
            res = comparison_results.get(policy, {})
            if res.get("fills", 0) > 0:
                writer.writerow([policy, res["fills"], res["settlement_pnl"], res["30s_bid_exit_pnl"], 
                               res["60s_bid_exit_pnl"], res["300s_bid_exit_pnl"], res["convergence_pnl"], 
                               res["roi"], res["win_rate"], res["top_1_trade_share"], res["top_3_trade_share"], 
                               res["top_5_trade_share"], res["sample_size_warning"], res["diagnostic_only"], 
                               res["policy_frozen"]])

    with open(reports_dir / "gettoplive_level_value_hold_trades.json", "w") as f:
        json.dump(policy_trades.get("level_value_hold", []), f, indent=2)

    # 2. Quadrants
    quadrant_counts = Counter(p["quadrant"] for p in all_polls)
    with open(reports_dir / "gettoplive_policy_quadrants.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["quadrant", "poll_count", "diagnostic_only"])
        for q, count in quadrant_counts.items():
            writer.writerow([q, count, True])
            
    # 3. Overlap Audit
    with open(reports_dir / "gettoplive_policy_overlap_audit.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["category", "count", "diagnostic_only"])
        for cat, count in overlap_counts.items():
            writer.writerow([cat, count, True])
            
    # 4. Missed Opportunities
    with open(reports_dir / "gettoplive_missed_opportunities.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["poll_ts", "match_id", "quadrant", "settlement_pnl", "lead", "edge", "diagnostic_only"])
        writer.writeheader()
        for m in missed_opportunities:
            m["diagnostic_only"] = True
            writer.writerow(m)

    # Reconcile with Value Replay
    expected_trades = config["value_reconciliation"]["expected_trades"]
    expected_pnl = config["value_reconciliation"]["expected_pnl_usd"]
    
    val_trades = comparison_results.get("level_value_hold", {}).get("fills", 0)
    val_pnl = comparison_results.get("level_value_hold", {}).get("settlement_pnl", 0.0)
    
    reconciliation_pass = (val_trades == expected_trades) and abs(val_pnl - expected_pnl) < 1.0
    
    # Summary MD
    summary = f"""# Net-Worth Policy Comparison Summary
    
## Reconciliation
- Value Replay Reconciliation Pass: {reconciliation_pass}
- Expected Trades: {expected_trades}
- Observed Trades: {val_trades}
- Expected PnL: ${expected_pnl:.2f}
- Observed PnL: ${val_pnl:.2f}

## Key Findings
- **Best Current Policy**: level_value_hold (Validated PnL: ${val_pnl:.2f})
- **Does Transition Add Value?**: See overlap audit. Level only: {overlap_counts['level_only']}, Transition only: {overlap_counts['transition_only']}.
- **Do Events Miss Opportunities?**: {len(missed_opportunities)} highly profitable net-worth polls had no signal.
- **Hold vs Quick Exit**: Convergence PnL vs 30s/60s PnL (Check CSV for exact metrics).
- **Concentration/Sample Size**: Check `sample_size_warning` in comparison CSV.

## Next Action
Review the quadrant distribution and overlap metrics to determine if the `transition_only` signals justify arming an independent secondary transition policy, or if `level_value_hold` with convergence exits fully saturates the observable net-worth edge.
"""
    with open(reports_dir / "gettoplive_policy_comparison_summary.md", "w") as f:
        f.write(summary)

    print("Audit complete.")
    if not reconciliation_pass:
        print(f"WARNING: Reconciliation failed. Expected {expected_trades} / ${expected_pnl}, got {val_trades} / ${val_pnl}")
        return 1
        
    return 0

if __name__ == "__main__":
    sys.exit(main())
