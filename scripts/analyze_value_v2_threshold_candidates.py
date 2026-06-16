#!/usr/bin/env python3
"""
Evaluate level_value_hold_v2_candidate threshold parameters.
Varies min_edge and max_edge while keeping other rules fixed.
Outputs comprehensive comparison metrics, trades, and reject summaries.
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

def main() -> int:
    config = load_config()
    params = _params()
    
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
        
    min_edges = [0.05, 0.075, 0.10, 0.125, 0.15]
    max_edges = sorted(list(set([0.20, 0.25, 0.30, params["max_edge"]])))
    min_fairs = [params["min_fair"]]
    
    candidates = []
    for me in min_edges:
        for maxe in max_edges:
            for mf in min_fairs:
                cid = f"minE_{me:.3f}_maxE_{maxe:.3f}_minF_{mf:.2f}"
                candidates.append({
                    "id": cid,
                    "min_edge": me,
                    "max_edge": maxe,
                    "min_fair": mf,
                    "is_baseline": (me == params["min_edge"] and maxe == params["max_edge"] and mf == params["min_fair"])
                })

    trades_per_candidate = defaultdict(list)
    rejects_per_candidate = defaultdict(Counter)
    causality_violations = Counter()
    
    print(f"Simulating {len(candidates)} threshold candidates...")
    
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
            # If all candidates entered, we can skip the rest of the match
            if all(entered_per_candidate.values()):
                break
                
            ns = int(row.get("received_at_ns") or 0)
            game_time = row.get("game_time_sec")
            lead = row.get("radiant_lead")
            
            if row.get("game_over") or game_time is None or lead is None:
                continue
                
            lead = int(lead)
            history.append((ns, lead))
            if game_time >= params["min_time"] and game_time <= params["max_time"]:
                value_bot_history.append((ns, lead))
                
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
            
            # Common Reason Resolution
            reason = None
            if game_time < params["min_time"]: reason = "game_too_early"
            elif game_time > params["max_time"]: reason = "game_too_late"
            elif abs(lead) < params["min_lead"]: reason = "lead_too_small"
            elif side is None: reason = "unknown_side_mapping"
            elif not entry_book: reason = "missing_book"
            elif book_age_ms > params["book_age_ms"]: reason = "book_stale"
            elif ask is None: reason = "missing_ask"
            elif ask > params["max_price"]: reason = "price_too_high"
            elif ask < params["min_price"]: reason = "price_too_low"
            elif abs(lead) > params["flip_lead"] and ask < params["flip_ask_floor"]: reason = "orientation_flip"
            elif manual_reason: reason = "manual_excluded"
            
            if reason:
                for cand in candidates:
                    cid = cand["id"]
                    if not entered_per_candidate[cid]:
                        rejects_per_candidate[cid][reason] += 1
                continue
                
            # Causality Check
            causality_valid = ns >= int(entry_book["received_at_ns"])
            if not causality_valid:
                for cand in candidates:
                    if not entered_per_candidate[cand["id"]]:
                        causality_violations[cand["id"]] += 1
                continue

            fair = fair_price(row, direction, lead, value_bot_history)
            edge = fair - ask
            
            for cand in candidates:
                cid = cand["id"]
                if entered_per_candidate[cid]:
                    continue
                    
                cand_reason = None
                if fair < cand["min_fair"]: cand_reason = "fair_too_low"
                elif edge < cand["min_edge"]: cand_reason = "edge_too_small"
                elif edge > cand["max_edge"]: cand_reason = "edge_too_large"
                
                if cand_reason:
                    rejects_per_candidate[cid][cand_reason] += 1
                else:
                    # WE HAVE A SIGNAL
                    entered_per_candidate[cid] = True
                    
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
                    
                    trades_per_candidate[cid].append({
                        "candidate_id": cid,
                        "poll_ts": ns,
                        "match_id": match_id,
                        "token_id": token,
                        "side": side,
                        "game_time_sec": game_time,
                        "lead": lead,
                        "entry_ask": ask,
                        "fair": fair,
                        "edge": edge,
                        "book_age_ms": book_age_ms,
                        "won": won,
                        "stake_usd": stake,
                        "settlement_pnl": settlement_pnl,
                        "convergence_pnl": pnl_conv,
                        "pnl_30s": pnl_30s,
                        "pnl_60s": pnl_60s,
                        "pnl_300s": pnl_300s
                    })

    if sum(causality_violations.values()) > 0:
        print("CRITICAL ERROR: Causality violations detected. Halting.")
        print(causality_violations)
        sys.exit(1)

    print("Generating candidate reports...")
    reports_dir = REPO_ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    
    baseline_cid = next((c["id"] for c in candidates if c["is_baseline"]), None)
    expected_trades = config["value_reconciliation"]["expected_trades"]
    expected_pnl = config["value_reconciliation"]["expected_pnl_usd"]
    
    baseline_reconciliation_pass = False
    if baseline_cid:
        b_trades = len(trades_per_candidate[baseline_cid])
        b_pnl = sum(t["settlement_pnl"] for t in trades_per_candidate[baseline_cid])
        if b_trades == expected_trades and abs(b_pnl - expected_pnl) < 1.0:
            baseline_reconciliation_pass = True
            
    if not baseline_reconciliation_pass:
        print(f"WARNING: Baseline reconciliation failed. Expected {expected_trades} trades / ${expected_pnl}. Observed {b_trades} / ${b_pnl}")
        sys.exit(1)

    candidate_results = []
    all_trades_dump = []
    
    for cand in candidates:
        cid = cand["id"]
        trades = trades_per_candidate[cid]
        fills = len(trades)
        
        if fills == 0:
            candidate_results.append({
                "candidate_id": cid,
                "min_edge": cand["min_edge"],
                "max_edge": cand["max_edge"],
                "min_fair": cand["min_fair"],
                "fills": 0
            })
            continue
            
        all_trades_dump.extend(trades)
        
        settlement_pnl_sum = sum(t["settlement_pnl"] for t in trades)
        pnl_30s_sum = sum(t["pnl_30s"] for t in trades if t["pnl_30s"] is not None)
        pnl_60s_sum = sum(t["pnl_60s"] for t in trades if t["pnl_60s"] is not None)
        pnl_300s_sum = sum(t["pnl_300s"] for t in trades if t["pnl_300s"] is not None)
        pnl_conv_sum = sum(t["convergence_pnl"] for t in trades if t["convergence_pnl"] is not None)
        
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
        
        warning = ""
        if fills < 20:
            warning = "LOW_SAMPLE_SIZE"
        elif t5 > 0.60:
            warning = "HIGH_CONCENTRATION"
            
        # Since we strictly take 1 trade per match, unique_matches and unique_episodes == fills
        unique_matches = len(set(t["match_id"] for t in trades))
        
        candidate_results.append({
            "candidate_id": cid,
            "min_edge": cand["min_edge"],
            "max_edge": cand["max_edge"],
            "min_fair": cand["min_fair"],
            "signals": fills,
            "fills": fills,
            "unique_matches": unique_matches,
            "unique_episodes": unique_matches, 
            "settlement_pnl": settlement_pnl_sum,
            "convergence_pnl": pnl_conv_sum,
            "pnl_30s": pnl_30s_sum,
            "pnl_60s": pnl_60s_sum,
            "pnl_300s": pnl_300s_sum,
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
            "sample_size_warning": warning,
            "concentration_warning": warning,
            "causality_violations": causality_violations[cid],
            "reconciliation_pass": True # If we didn't exit, the baseline passed, so all sharing rules passed
        })

    # Output JSON
    with open(reports_dir / "value_v2_threshold_candidates.json", "w") as f:
        json.dump(candidate_results, f, indent=2)

    # Output CSV Comparison
    if candidate_results:
        with open(reports_dir / "value_v2_threshold_candidates.csv", "w", newline="") as f:
            headers = [k for k in candidate_results[0].keys()]
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(candidate_results)

    # Output Trades
    if all_trades_dump:
        with open(reports_dir / "value_v2_candidate_trades.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(all_trades_dump[0].keys()))
            writer.writeheader()
            writer.writerows(all_trades_dump)
            
    # Output Rejects
    with open(reports_dir / "value_v2_candidate_rejects.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["candidate_id", "reject_reason", "count"])
        for cid, counts in rejects_per_candidate.items():
            for reason, count in counts.items():
                writer.writerow([cid, reason, count])

    # Summary Markdown
    best_cand = None
    best_score = -999
    
    for c in candidate_results:
        if c.get("fills", 0) < 20: continue
        if c.get("top_5_trade_share", 1.0) > 0.60: continue
        if c.get("causality_violations", 0) > 0: continue
        
        # Scoring: want to beat baseline PnL, maintain DD, increase unique episodes
        # Simple heuristic: maximize PnL if constraints met
        if c["settlement_pnl"] > best_score:
            best_score = c["settlement_pnl"]
            best_cand = c

    summary_lines = [
        "# Value Bot v2 Threshold Candidate Study\n",
        "## Baseline Reconciliation",
        f"- Reconciliation Pass: {baseline_reconciliation_pass}",
        f"- Expected Baseline PnL: ${expected_pnl:.2f}",
        f"- Observed Baseline PnL: ${b_pnl:.2f}\n" if baseline_cid else "- No baseline candidate found.",
        "## Recommended Candidate",
    ]
    
    if best_cand:
        summary_lines.extend([
            f"- **Candidate ID**: {best_cand['candidate_id']}",
            f"- **Parameters**: min_edge={best_cand['min_edge']}, max_edge={best_cand['max_edge']}",
            f"- **Fills / Unique Episodes**: {best_cand['fills']}",
            f"- **Settlement PnL**: ${best_cand['settlement_pnl']:.2f}",
            f"- **Win Rate**: {best_cand['win_rate']:.1%}",
            f"- **Max Drawdown**: ${best_cand['max_drawdown']:.2f}",
            f"- **ROI**: {best_cand['ROI']:.1%}"
        ])
    else:
        summary_lines.append("- No candidate passed the strict sample-size and concentration constraints.")
        
    summary_lines.append("\n## Analysis")
    summary_lines.append("See `value_v2_threshold_candidates.csv` for the full parameter grid.")
    
    with open(reports_dir / "value_v2_candidate_summary.md", "w") as f:
        f.write("\n".join(summary_lines))

    print(f"Generated v2 threshold study. Best candidate: {best_cand['candidate_id'] if best_cand else 'None'}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
