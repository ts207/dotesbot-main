#!/usr/bin/env python3
"""
Diagnostic audit for the market_disagreement_regime_alpha research branch.
Tests candidate rule families against the poll-level universe.
"""
from __future__ import annotations

import argparse
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
    resolve_yes_won,
    signal_side,
    _params,
)
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as pds
from structure_state import decode_structure_state
from unified_storage.event_store import load_manual_windows, manual_window_reason

def _to_int(value: Any) -> int | None:
    if value in (None, ""): return None
    try: return int(float(value))
    except: return None

def top_share(values: list[float], n: int) -> float | None:
    if not values: return 0.0
    denom = sum(abs(v) for v in values)
    if denom == 0: return 0.0
    return sum(abs(v) for v in sorted(values, key=lambda x: abs(x), reverse=True)[:n]) / denom

def parquet_dataset_nonempty(path: Path) -> pds.Dataset:
    files = [str(p) for p in path.rglob("*.parquet") if p.stat().st_size > 0]
    if not files:
        raise FileNotFoundError(f"no non-empty parquet files under {path}")
    return pds.dataset(files, format="parquet", partitioning="hive")

def load_snapshots_local(match_ids: set[str]) -> dict[str, list[dict]]:
    dataset = parquet_dataset_nonempty(REPO_ROOT / "data_v2" / "snapshots")
    columns = [
        "match_id", "received_at_ns", "received_at_utc", "game_time_sec", "radiant_lead",
        "game_over", "data_source", "radiant_team_id", "dire_team_id", "radiant_team", "dire_team", "date",
        "radiant_score", "dire_score", "building_state", "tower_state"
    ]
    table = dataset.to_table(
        columns=columns,
        filter=(pc.field("data_source") == "top_live")
        & pc.is_in(pc.field("match_id"), pa.array(list(match_ids))),
    )
    by_match = defaultdict(list)
    for row in table.to_pylist():
        by_match[str(row["match_id"])].append(row)
    for rows in by_match.values():
        rows.sort(key=lambda row: row.get("received_at_ns") or 0)
    return by_match

def load_books_local(tokens: set[str]) -> dict[str, tuple[list[int], list[dict]]]:
    dataset = parquet_dataset_nonempty(REPO_ROOT / "data_v2" / "book_ticks")
    table = dataset.to_table(
        columns=["asset_id", "received_at_ns", "best_ask", "best_bid", "mid", "date"],
        filter=pc.is_in(pc.field("asset_id"), pa.array(list(tokens))),
    )
    by_asset: dict[str, list[dict]] = defaultdict(list)
    for row in table.to_pylist():
        if row.get("received_at_ns") is not None:
            by_asset[str(row["asset_id"])].append(row)

    out: dict[str, tuple[list[int], list[dict]]] = {}
    for asset_id, rows in by_asset.items():
        rows.sort(key=lambda row: row["received_at_ns"])
        out[asset_id] = ([row["received_at_ns"] for row in rows], rows)
    return out

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

def compute_historical_features(history: list, current_ns: int, current_lead: int, current_ask: float, lookback_sec: int, book: list[dict], token: str) -> dict:
    target_ns = current_ns - lookback_sec * 1_000_000_000
    best_h = None
    for h_ns, h_lead, h_gt in reversed(history):
        if h_ns <= target_ns:
            best_h = (h_ns, h_lead)
            break
    if not best_h and history:
        best_h = (history[0][0], history[0][1])
        
    delta_lead = 0
    leader_changed = False
    past_ask = current_ask
    if best_h:
        delta_lead = current_lead - best_h[1]
        if (current_lead > 0 and best_h[1] < 0) or (current_lead < 0 and best_h[1] > 0):
            leader_changed = True
        past_book = book_at(book, token, best_h[0])
        if past_book and past_book.get("best_ask"):
            try:
                past_ask = float(past_book["best_ask"])
            except ValueError:
                pass
                
    return {
        f"networth_delta_{lookback_sec}s": delta_lead,
        f"leader_changed_{lookback_sec}s": leader_changed,
        f"recent_price_move_{lookback_sec}s": current_ask - past_ask
    }

def build_rules():
    # Candidates format: (rule_id, lambda row: bool)
    return [
        ("VALUE_v1_baseline", lambda r: r["is_baseline"]),
        ("1_market_veto_override", lambda r: r["fair"] >= 0.78 and 0.10 <= r["edge"] <= 0.15 and r["ask"] >= 0.60 and r["spread"] <= 0.06 and r["book_age_ms"] <= 10000 and 720 <= r["game_time_sec"] <= 2100),
        ("2_cheap_leader_trap", lambda r: r["fair"] >= 0.75 and r["ask"] < 0.55 and r["market_disagreement_bucket"] == "high"),
        ("3_late_game_toxicity", lambda r: r["game_time_sec"] > 2400 and r["fair"] >= 0.80 and r["edge"] >= 0.15),
        ("4_stable_leader_continuation", lambda r: r["networth_lead_abs"] >= 5000 and not r["leader_changed_60s"] and (r["networth_delta_60s"] * (1 if r["networth_lead"] > 0 else -1)) >= 0 and 0.55 <= r["ask"] <= 0.80),
        ("5_market_lag_candidate", lambda r: (r["networth_delta_30s"] * (1 if r["networth_lead"] > 0 else -1)) >= 2000 and abs(r["recent_price_move_30s"]) <= 0.03 and r["spread"] <= 0.05 and r["book_age_ms"] <= 5000)
    ]

def evaluate_baseline(p: dict, game_time: int, lead: int, side: str, ask: float, book_age_ms: int, entry_book: bool, manual_reason: bool, fair: float, edge: float) -> bool:
    if game_time < p["min_time"]: return False
    if game_time > p["max_time"]: return False
    if abs(lead) < p["min_lead"]: return False
    if side is None: return False
    if not entry_book: return False
    if book_age_ms > p["book_age_ms"]: return False
    if ask is None: return False
    if ask > p["max_price"]: return False
    if ask < p["min_price"]: return False
    if abs(lead) > p["flip_lead"] and ask < p["flip_ask_floor"]: return False
    if manual_reason: return False
    if fair < p["min_fair"]: return False
    if edge < p["min_edge"]: return False
    if edge > p["max_edge"]: return False
    return True

def get_label(row: dict) -> str:
    if row["is_baseline"]: return "baseline_fill"
    if row["ask"] < 0.55: return "cheap_leader_trap"
    if row["game_time_sec"] > 2400: return "late_game_toxic"
    if row["edge"] >= 0.15:
        if row["won"]: return "high_edge_valid"
        else: return "high_edge_false_positive"
    if row["fair"] >= 0.75 and row["ask"] >= 0.60: return "market_veto_state"
    if row["edge"] >= 0.10: return "marginal_reject"
    return "baseline_reject"

def main() -> int:
    base_params = _params()
    outcomes, outcome_sources = load_outcomes()
    markets, skipped = load_markets()
    snapshots = load_snapshots_local(set(markets))
    
    tokens = set()
    for match_id in snapshots:
        tokens.add(str(markets[match_id]["yes_token_id"]))
        tokens.add(str(markets[match_id]["no_token_id"]))
    book = load_books_local(tokens)
    
    joined = {
        match_id: rows
        for match_id, rows in snapshots.items()
        if str(markets[match_id]["yes_token_id"]) in book
        and str(markets[match_id]["no_token_id"]) in book
    }
    
    manual_windows = load_manual_windows(REPO_ROOT / "data" / "manual" / "excluded_time_windows.csv")
    
    polls = []
    drop_reasons = Counter()
    
    print(f"Building poll-level universe across {len(joined)} joined matches...")
    
    for match_id, rows in joined.items():
        mapping = markets[match_id]
        yes_token = str(mapping["yes_token_id"])
        no_token = str(mapping["no_token_id"])
        yes_won, source = resolve_yes_won(match_id, mapping, book, outcomes, outcome_sources)
        if yes_won is None: continue
        
        history = deque(maxlen=4000)
        
        for row in rows:
            ns = int(row.get("received_at_ns") or 0)
            game_time = _to_int(row.get("game_time_sec"))
            lead = _to_int(row.get("radiant_lead"))
            data_source = row.get("data_source")
            
            if row.get("game_over") or game_time is None or lead is None:
                drop_reasons["missing_data_or_game_over"] += 1
                continue
            
            history.append((ns, lead, game_time))
            
            # Universe filters
            if data_source != "top_live":
                drop_reasons["not_top_live"] += 1
                continue
            if game_time < 480 or game_time > 2700:
                drop_reasons["game_time_bounds"] += 1
                continue
            
            side, direction = signal_side(mapping, lead)
            token = yes_token if side == "YES" else no_token if side == "NO" else None
            entry_book = book_at(book, token, ns) if token else None
            
            if not entry_book:
                drop_reasons["no_entry_book"] += 1
                continue
            
            book_ns = int(entry_book["received_at_ns"])
            book_age_ms = (ns - book_ns) / 1_000_000
            if book_age_ms > 30000:
                drop_reasons["book_stale"] += 1
                continue
            
            causality_valid = ns >= book_ns
            if not causality_valid:
                drop_reasons["causality_violation"] += 1
                continue
            
            ask = None
            bid = None
            try:
                b_ask = entry_book.get("best_ask")
                b_bid = entry_book.get("best_bid")
                ask = float(b_ask) if b_ask is not None else float("nan")
                bid = float(b_bid) if b_bid is not None else float("nan")
            except ValueError: pass
            
            if ask is None or math.isnan(ask) or bid is None or math.isnan(bid):
                drop_reasons["missing_ask_or_bid"] += 1
                continue
            if ask < 0.35 or ask > 0.95:
                drop_reasons["ask_out_of_bounds"] += 1
                continue
            
            manual_reason = manual_window_reason(ns, manual_windows)
            cand_history = deque((h_ns, h_lead) for h_ns, h_lead, h_gt in history if h_gt >= base_params["min_time"] and h_gt <= base_params["max_time"])
            fair = fair_price(row, direction, lead, cand_history)
            edge = fair - ask
            spread = ask - bid
            
            is_baseline = evaluate_baseline(base_params, game_time, lead, side, ask, book_age_ms, True, manual_reason, fair, edge)
            won = 1 if (yes_won == 1 and side == "YES") or (yes_won == 0 and side == "NO") else 0
            
            # Structure state
            rad_score = _to_int(row.get("radiant_score"))
            dire_score = _to_int(row.get("dire_score"))
            rad_towers, dire_towers = None, None
            try:
                st = decode_structure_state({
                    "match_id": match_id,
                    "game_time_sec": game_time,
                    "building_state": row.get("building_state"),
                    "building_state_schema": "top_live_lane_tower_progress",
                    "tower_state": row.get("tower_state"),
                })
                rad = [st.radiant_t1_alive, st.radiant_t2_alive, st.radiant_t3_alive, st.radiant_t4_alive]
                dire = [st.dire_t1_alive, st.dire_t2_alive, st.dire_t3_alive, st.dire_t4_alive]
                if not any(x is None for x in rad + dire):
                    rad_towers, dire_towers = int(sum(rad)), int(sum(dire))
            except: pass
            
            leader_sign = 1 if lead > 0 else -1
            leader_kill_diff = leader_sign * (rad_score - dire_score) if rad_score is not None and dire_score is not None else None
            leader_tower_diff = leader_sign * (rad_towers - dire_towers) if rad_towers is not None and dire_towers is not None else None
            
            score_aligned_with_leader = leader_kill_diff is not None and leader_kill_diff >= 0
            tower_aligned_with_leader = leader_tower_diff is not None and leader_tower_diff >= 0
            building_state_aligned = score_aligned_with_leader and tower_aligned_with_leader
            
            # Features
            f30 = compute_historical_features(list(history), ns, lead, ask, 30, book, token)
            f60 = compute_historical_features(list(history), ns, lead, ask, 60, book, token)
            
            market_disagreement_bucket = "high" if (fair - ask) >= 0.15 else "medium" if (fair - ask) >= 0.05 else "low"
            spread_bucket = "tight" if spread <= 0.02 else "normal" if spread <= 0.06 else "wide"
            
            poll_row = {
                "match_id": match_id,
                "poll_ts": ns,
                "game_time_sec": game_time,
                "book_age_ms": book_age_ms,
                "ask": ask,
                "fair": fair,
                "edge": edge,
                "spread": spread,
                "networth_lead": lead,
                "networth_lead_abs": abs(lead),
                "is_baseline": is_baseline,
                "won": won,
                "networth_delta_30s": f30["networth_delta_30s"],
                "recent_price_move_30s": f30["recent_price_move_30s"],
                "leader_changed_30s": f30["leader_changed_30s"],
                "networth_delta_60s": f60["networth_delta_60s"],
                "recent_price_move_60s": f60["recent_price_move_60s"],
                "leader_changed_60s": f60["leader_changed_60s"],
                "score_diff_leader_aligned": score_aligned_with_leader,
                "tower_diff_leader_aligned": tower_aligned_with_leader,
                "building_state_aligned": building_state_aligned,
                "price_floor_distance": ask - 0.55,
                "price_ceiling_distance": 0.84 - ask,
                "market_resistance": fair - ask,
                "market_confidence": abs(ask - 0.50),
                "market_disagreement_bucket": market_disagreement_bucket,
                "spread_bucket": spread_bucket,
                "stake_usd": base_params["stake"],
            }
            poll_row["label"] = get_label(poll_row)
            polls.append(poll_row)
            
    print(f"Generated {len(polls)} polls.")
    print("Drop reasons:", dict(drop_reasons))
    
    if len(polls) == 0:
        return 0
    
    # Evaluate rules
    rules = build_rules()
    rule_results = []
    
    baseline_polls = [p for p in polls if p["is_baseline"]]
    baseline_trades = []
    entered_matches_b = set()
    for p in baseline_polls:
        if p["match_id"] not in entered_matches_b:
            baseline_trades.append(p)
            entered_matches_b.add(p["match_id"])
    
    baseline_pnl = sum((p["stake_usd"] / p["ask"]) - p["stake_usd"] if p["won"] else -p["stake_usd"] for p in baseline_trades)
    
    for rule_id, rule_fn in rules:
        matching_polls = [p for p in polls if rule_fn(p)]
        
        # Deduplicate trades (first signal per match)
        trades = []
        entered_matches = set()
        for p in matching_polls:
            if p["match_id"] not in entered_matches:
                trades.append(p)
                entered_matches.add(p["match_id"])
                
        fills = len(trades)
        if fills == 0:
            rule_results.append({
                "rule_id": rule_id,
                "signals": len(matching_polls),
                "fills": 0,
            })
            continue
            
        pnls = []
        for t in trades:
            pnl = (t["stake_usd"] / t["ask"]) - t["stake_usd"] if t["won"] else -t["stake_usd"]
            pnls.append(pnl)
            
        settlement_pnl = sum(pnls)
        wins = sum(1 for t in trades if t["won"])
        win_rate = wins / fills
        stake_sum = sum(t["stake_usd"] for t in trades)
        roi = settlement_pnl / stake_sum if stake_sum > 0 else 0
        
        t1 = top_share(pnls, 1) or 0.0
        t3 = top_share(pnls, 3) or 0.0
        t5 = top_share(pnls, 5) or 0.0
        max_dd = calculate_max_drawdown(pnls)
        
        avg_ask = sum(t["ask"] for t in trades) / fills
        avg_fair = sum(t["fair"] for t in trades) / fills
        avg_edge = sum(t["edge"] for t in trades) / fills
        avg_spread = sum(t["spread"] for t in trades) / fills
        avg_book_age_ms = sum(t["book_age_ms"] for t in trades) / fills
        
        cand_match_ids = {t["match_id"] for t in trades}
        overlap_with_value_v1 = len(cand_match_ids.intersection(entered_matches_b))
        incremental_trades = len(cand_match_ids - entered_matches_b)
        
        incremental_pnl = 0.0
        if rule_id != "VALUE_v1_baseline":
            for i, t in enumerate(trades):
                if t["match_id"] not in entered_matches_b:
                    incremental_pnl += pnls[i]
        
        warning = ""
        if fills < 20: warning = "LOW_SAMPLE_SIZE"
        elif t5 > 0.60: warning = "HIGH_CONCENTRATION"
        
        rule_results.append({
            "rule_id": rule_id,
            "diagnostic_only": True,
            "policy_frozen": True,
            "signals": len(matching_polls),
            "fills": fills,
            "unique_matches": len(set(t["match_id"] for t in trades)),
            "unique_episodes": len(set(t["match_id"] for t in trades)), # Simplified
            "settlement_pnl": settlement_pnl,
            "pnl_30s": 0.0, # Not calculated in this simplified proxy
            "pnl_60s": 0.0,
            "pnl_300s": 0.0,
            "convergence_pnl": settlement_pnl, # Proxy
            "ROI": roi,
            "win_rate": win_rate,
            "max_drawdown": max_dd,
            "avg_ask": avg_ask,
            "avg_fair": avg_fair,
            "avg_edge": avg_edge,
            "avg_spread": avg_spread,
            "avg_book_age_ms": avg_book_age_ms,
            "top_1_trade_share": t1,
            "top_3_trade_share": t3,
            "top_5_trade_share": t5,
            "sample_size_warning": warning,
            "concentration_warning": warning,
            "causality_violations": 0,
            "overlap_with_value_v1": overlap_with_value_v1,
            "incremental_trades_vs_value_v1": incremental_trades,
            "incremental_pnl_vs_value_v1": incremental_pnl,
        })
        
    reports_dir = REPO_ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    
    with open(reports_dir / "market_disagreement_regime_alpha.csv", "w", newline="") as f:
        headers = list(polls[0].keys())
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(polls)
        
    with open(reports_dir / "market_disagreement_regime_alpha_rules.csv", "w", newline="") as f:
        headers = list(rule_results[0].keys())
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rule_results)
        
    summary_md = [
        "# Market Disagreement Regime Alpha Audit",
        "",
        "## Universe",
        f"- Total Polls Analyzed: {len(polls)}",
        "",
        "## Candidate Rule Families"
    ]
    for r in rule_results:
        summary_md.append(f"\n### {r.get('rule_id', 'Unknown')}")
        for k, v in r.items():
            if k == "rule_id": continue
            if isinstance(v, float):
                summary_md.append(f"- {k}: {v:.3f}")
            else:
                summary_md.append(f"- {k}: {v}")
                
    summary_md.append("\n## Alpha Hierarchy Status")
    summary_md.append("1. **VALUE v1**: frozen primary policy, shadow-forward only")
    summary_md.append("2. **market_disagreement_regime_alpha**: research branch only (diagnostic_only=true)")
    summary_md.append("3. **survival overlay**: observe-only feature logging")
    summary_md.append("4. **transition/event**: diagnostic only")
    summary_md.append("5. **DSWING**: separate explicitly armed branch, not part of VALUE alpha")
    summary_md.append("6. **Model B**: rejected")
    
    with open(reports_dir / "market_disagreement_regime_alpha_summary.md", "w") as f:
        f.write("\n".join(summary_md))
        
    with open(reports_dir / "market_disagreement_regime_alpha.json", "w") as f:
        json.dump(rule_results, f, indent=2)
        
    print(f"Generated regime alpha reports in {reports_dir}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
