#!/usr/bin/env python3
"""
Cluster missed opportunities into episodes and diagnose the specific rules
that blocked them from becoming valid Value signals.
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
    load_snapshots,
    signal_side,
    _params,
)
from unified_storage.event_store import load_manual_windows, manual_window_reason

def get_future_book(times: list[int], rows: list[dict], ns_start: int, delta_ns: int) -> dict | None:
    target_ns = ns_start + delta_ns
    idx = bisect.bisect_right(times, target_ns) - 1
    if idx < 0:
        return None
    return rows[idx]

def main():
    missed_csv = REPO_ROOT / "reports" / "gettoplive_missed_opportunities.csv"
    if not missed_csv.exists():
        print(f"File not found: {missed_csv}")
        return 1

    with open(missed_csv, "r") as f:
        reader = csv.DictReader(f)
        missed_rows = list(reader)

    if not missed_rows:
        print("No missed opportunities found.")
        return 0

    match_ids = {r["match_id"] for r in missed_rows}
    
    print(f"Loading data for {len(match_ids)} matches...")
    markets, _ = load_markets()
    snapshots = load_snapshots(match_ids)
    
    tokens = set()
    for match_id in match_ids:
        if match_id in markets:
            tokens.add(str(markets[match_id]["yes_token_id"]))
            tokens.add(str(markets[match_id]["no_token_id"]))
    book = load_books(tokens)
    
    params = _params()
    manual_windows = load_manual_windows(REPO_ROOT / "data" / "manual" / "excluded_time_windows.csv")
    
    print("Evaluating detailed block reasons...")
    enriched_polls = []
    
    for row in missed_rows:
        match_id = row["match_id"]
        poll_ts = int(row["poll_ts"])
        settlement_pnl = float(row["settlement_pnl"])
        
        mapping = markets.get(match_id)
        if not mapping:
            continue
            
        yes_token = str(mapping["yes_token_id"])
        no_token = str(mapping["no_token_id"])
        
        # We need the history up to poll_ts to calculate fair accurately
        match_snaps = snapshots.get(match_id, [])
        history = deque(maxlen=4000)
        value_bot_history = deque(maxlen=4000)
        
        target_snap = None
        for snap in match_snaps:
            ns = int(snap.get("received_at_ns") or 0)
            lead = snap.get("radiant_lead")
            game_time = snap.get("game_time_sec")
            if snap.get("game_over") or game_time is None or lead is None:
                continue
                
            lead = int(lead)
            history.append((ns, lead))
            if game_time >= params["min_time"] and game_time <= params["max_time"]:
                value_bot_history.append((ns, lead))
                
            if ns == poll_ts:
                target_snap = snap
                break
                
        if not target_snap:
            continue
            
        game_time = target_snap["game_time_sec"]
        lead = int(target_snap["radiant_lead"])
        side, direction = signal_side(mapping, lead)
        
        token = yes_token if side == "YES" else no_token if side == "NO" else None
        entry_book = book_at(book, token, poll_ts) if token else None
        
        ask = None
        bid = None
        spread = None
        book_age_ms = None
        if entry_book:
            book_age_ms = (poll_ts - int(entry_book["received_at_ns"])) / 1_000_000
            a = entry_book.get("best_ask")
            b = entry_book.get("best_bid")
            if a is not None and not math.isnan(float(a)):
                ask = float(a)
            if b is not None and not math.isnan(float(b)):
                bid = float(b)
            if ask is not None and bid is not None:
                spread = ask - bid
                
        fair = fair_price(target_snap, direction, lead, value_bot_history) if side else 0.0
        edge = fair - ask if ask is not None else 0.0
        
        manual_reason = manual_window_reason(poll_ts, manual_windows)
        
        reason = "unknown"
        if game_time < params["min_time"]: reason = "game_time_too_early"
        elif game_time > params["max_time"]: reason = "game_time_too_late"
        elif abs(lead) < params["min_lead"]: reason = "lead_too_small"
        elif side is None: reason = "unknown_side_mapping"
        elif not entry_book: reason = "missing_book"
        elif book_age_ms is not None and book_age_ms > params["book_age_ms"]: reason = "book_too_old"
        elif ask is None: reason = "missing_ask"
        elif ask > params["max_price"]: reason = "price_too_high"
        elif ask < params["min_price"]: reason = "price_too_low"
        elif abs(lead) > params["flip_lead"] and ask < params["flip_ask_floor"]: reason = "anti_flip_floor_blocked"
        elif manual_reason: reason = "manual_excluded"
        elif fair < params["min_fair"]: reason = "fair_too_low"
        elif edge < params["min_edge"]: reason = "edge_too_small"
        elif edge > params["max_edge"]: reason = "edge_cap_blocked"
        else: reason = "duplicate_position_blocked" # Assuming it passed everything else
        
        enriched_polls.append({
            "match_id": match_id,
            "side": side,
            "poll_ts": poll_ts,
            "game_time": game_time,
            "lead": lead,
            "ask": ask,
            "spread": spread,
            "book_age_ms": book_age_ms,
            "settlement_pnl": settlement_pnl,
            "reason_no_signal": reason
        })

    print("Clustering into episodes...")
    # Sort by match_id, side, then poll_ts
    enriched_polls.sort(key=lambda x: (x["match_id"], x["side"] or "", x["poll_ts"]))
    
    episodes = []
    current_episode = []
    
    for p in enriched_polls:
        if not current_episode:
            current_episode.append(p)
            continue
            
        last_p = current_episode[-1]
        
        same_match = p["match_id"] == last_p["match_id"]
        same_side = p["side"] == last_p["side"]
        gap_ns = p["poll_ts"] - last_p["poll_ts"]
        
        if same_match and same_side and gap_ns <= 120_000_000_000:
            current_episode.append(p)
        else:
            episodes.append(current_episode)
            current_episode = [p]
            
    if current_episode:
        episodes.append(current_episode)
        
    print(f"Found {len(episodes)} episodes.")
    
    episode_reports = []
    for idx, ep in enumerate(episodes):
        match_id = ep[0]["match_id"]
        side = ep[0]["side"]
        
        best_pnl = max(p["settlement_pnl"] for p in ep)
        
        valid_asks = [p["ask"] for p in ep if p["ask"] is not None]
        avg_ask = sum(valid_asks)/len(valid_asks) if valid_asks else None
        
        valid_spreads = [p["spread"] for p in ep if p["spread"] is not None]
        avg_spread = sum(valid_spreads)/len(valid_spreads) if valid_spreads else None
        
        valid_ages = [p["book_age_ms"] for p in ep if p["book_age_ms"] is not None]
        avg_age = sum(valid_ages)/len(valid_ages) if valid_ages else None
        
        avg_lead = sum(p["lead"] for p in ep) / len(ep)
        avg_time = sum(p["game_time"] for p in ep) / len(ep)
        
        reasons = [p["reason_no_signal"] for p in ep]
        most_common_reason = Counter(reasons).most_common(1)[0][0]
        
        episode_reports.append({
            "episode_id": idx + 1,
            "match_id": match_id,
            "side": side,
            "poll_count": len(ep),
            "best_pnl": best_pnl,
            "avg_entry_ask": avg_ask,
            "avg_networth_lead": avg_lead,
            "avg_game_time": avg_time,
            "avg_spread": avg_spread,
            "avg_book_age_ms": avg_age,
            "reason_no_signal": most_common_reason
        })

    # Global summary
    unique_matches = len(set(e["match_id"] for e in episode_reports))
    
    summary = {
        "episode_count": len(episode_reports),
        "unique_markets": unique_matches, # Assuming 1 market pair per match for this level
        "unique_matches": unique_matches,
        "avg_best_pnl": sum(e["best_pnl"] for e in episode_reports) / len(episode_reports) if episode_reports else 0,
        "reasons": dict(Counter(e["reason_no_signal"] for e in episode_reports))
    }
    
    out_dir = REPO_ROOT / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    
    with open(out_dir / "gettoplive_missed_opportunity_episodes.json", "w") as f:
        json.dump(summary, f, indent=2)
        
    with open(out_dir / "gettoplive_missed_opportunity_episodes.csv", "w", newline="") as f:
        if not episode_reports:
            return 0
        writer = csv.DictWriter(f, fieldnames=list(episode_reports[0].keys()))
        writer.writeheader()
        writer.writerows(episode_reports)
        
    print(f"Generated {out_dir / 'gettoplive_missed_opportunity_episodes.csv'}")
    print(f"Summary: {summary}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
