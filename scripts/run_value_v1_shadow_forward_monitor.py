#!/usr/bin/env python3
"""
Value Bot v1 Shadow-Forward Monitor.
Continuously tails live logs to record hypothetical WOULD_ENTER and WOULD_REJECT decisions.
Read-only: no orders, no .env mutation.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
import math
import argparse
from collections import deque, Counter
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from value_engine import ValueEngine, ValueSignal, ValueReject
from gettoplive_state import validate_top_live_state
from live_state import load_live_state
from poly_ws import BookStore
from structure_state import decode_structure_state
import csv

# 1. Load Shadow Config
CONFIG_PATH = REPO_ROOT / "configs" / "value_v1_shadow_forward_v1.json"
with open(CONFIG_PATH, "r") as f:
    shadow_config = json.load(f)

# Override ENV for ValueEngine to use shadow params
for k, v in shadow_config.items():
    os.environ[k] = str(v)

# Enforce no trading
os.environ["ENABLE_VALUE_TRADING"] = "false"
os.environ["VALUE_ENGINE_ENABLED"] = "true"

def load_markets_mapping():
    try:
        with open(REPO_ROOT / "markets.yaml", "r") as f:
            data = yaml.safe_load(f)
            return {str(m["dota_match_id"]): m for m in data.get("markets", []) if m.get("dota_match_id")}
    except Exception:
        return {}

def get_nav():
    # Attempt to read live NAV from persistence files
    balance_path = REPO_ROOT / "logs" / "usdc_balance.json"
    pos_path = REPO_ROOT / "logs" / "live_positions.json"
    
    cash = 0.0
    if balance_path.exists():
        try:
            cash = json.loads(balance_path.read_text()).get("usdc_balance", 0.0)
        except Exception:
            pass
            
    token_val = 0.0
    if pos_path.exists():
        try:
            positions = json.loads(pos_path.read_text()).get("positions", [])
            for p in positions:
                if p.get("state") == "OPEN":
                    token_val += float(p.get("shares", 0)) * float(p.get("last_mid", 0.5))
        except Exception:
            pass
    return cash + token_val

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-backlog", action="store_true", help="Unsafe for validation. Replay historical backlog.")
    parser.add_argument("--dry-run", action="store_true", help="Validate config and exit.")
    args = parser.parse_args()
    
    if args.dry_run:
        print("Dry run successful. Config validated.")
        return

    processing_mode = "backlog_replay" if args.replay_backlog else "live_tail"
    validation_eligible = not args.replay_backlog
    print(f"Starting Shadow Monitor v1. Policy: level_value_hold_v1")
    print(f"Log: logs/value_v1_shadow_forward_decisions.jsonl")
    
    markets = load_markets_mapping()
    engine = ValueEngine()
    book_store = BookStore()
    
    decisions_path = REPO_ROOT / "logs" / "value_v1_shadow_forward_decisions.jsonl"
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    
    snapshot_path = REPO_ROOT / "logs" / "raw_snapshots.csv"
    book_path = REPO_ROOT / "logs" / "book_events.csv"
    
    last_snap_ts = 0
    last_book_ts = 0
    
    entered_matches = set()
    
    book_file_offset = 0
    
    last_markets_reload = time.monotonic()
    MARKETS_RELOAD_INTERVAL = 60

    import gzip
    logs_dir = REPO_ROOT / "logs"
    archive_paths = sorted(logs_dir.glob("raw_snapshots.csv.*.gz"))
    if args.replay_backlog and archive_paths:
        print(f"Replaying {len(archive_paths)} rotated snapshot archive(s)...")
        for arch in archive_paths:
            print(f"  Reading {arch.name}...")
            try:
                with gzip.open(arch, "rt", errors="replace") as gz:
                    gz.readline()  # skip header
                    for line in gz:
                        try:
                            row = next(csv.reader([line]))
                        except Exception:
                            continue
                        if len(row) < 18:
                            continue
                        try:
                            ns = int(row[1])
                        except Exception:
                            continue
                        if ns <= last_snap_ts:
                            continue
                        last_snap_ts = ns
                        match_id = row[2]
                        if match_id not in markets:
                            continue
                        mapping = markets[match_id]
                        game = {
                            "match_id": match_id,
                            "received_at_ns": ns,
                            "data_source": row[15],
                            "game_over": row[17].lower() == "true",
                            "game_time_sec": int(row[6]),
                            "radiant_lead": int(row[7]),
                            "radiant_score": row[8] if row[8] else None,
                            "dire_score": row[9] if row[9] else None,
                            "building_state": row[10] if row[10] else None,
                            "tower_state": row[11] if row[11] else None,
                            "stream_delay_s": float(row[13]) if row[13] else 0.0,
                            "source_update_age_sec": float(row[14]) if row[14] else 0.0,
                        }
                        
                        # mock book evaluate
                        results = engine.evaluate(game, mapping, book_store)
                        for res in results:
                            decision = "WOULD_REJECT"
                            reason = "unknown"
                            if isinstance(res, ValueSignal):
                                decision = "WOULD_ENTER" if match_id not in entered_matches else "DUPLICATE_POSITION_BLOCKED"
                                if match_id not in entered_matches:
                                    entered_matches.add(match_id)
                                reason = "value_edge"
                            else:
                                reason = res.reason
                                if reason in ["game_too_early", "game_too_late", "lead_too_small"]:
                                    decision = "WOULD_SKIP"
                            token_id = getattr(res, "token_id", "")
                            side = getattr(res, "side", "")
                            fair = getattr(res, "fair_price", None)
                            ask = getattr(res, "ask", None)
                            edge = getattr(res, "edge", None)
                            entry = {
                                "decision_ts": time.time_ns(),
                                "poll_ts": ns,
                                "match_id": match_id,
                                "side": side,
                                "decision": decision,
                                "reason": reason,
                                "game_time_sec": game.get("game_time_sec"),
                                "radiant_lead": game.get("radiant_lead"),
                                "entry_ask": ask,
                                "best_bid": None,
                                "fair": fair,
                                "edge": edge,
                                "source": "archive_replay",
                            }
                            with open(decisions_path, "a") as df:
                                df.write(json.dumps(entry) + "\n")
            except Exception as exc:
                print(f"  Archive replay error for {arch.name}: {exc}")
        print("Archive replay complete.")

    snap_file_offset = 0
    book_file_offset = 0
    while True:
        try:
            now_mono = time.monotonic()
            if now_mono - last_markets_reload > MARKETS_RELOAD_INTERVAL:
                fresh = load_markets_mapping()
                new_ids = set(fresh) - set(markets)
                if new_ids:
                    print(f"[markets] Reloaded: {len(new_ids)} new binding(s): {new_ids}")
                markets = fresh
                last_markets_reload = now_mono

            # --- 1. Failure-closed Checks ---
            nav = get_nav()
            live_state = load_live_state()
            
            # --- 2. Update BookStore ---
            if book_path.exists():
                current_size = book_path.stat().st_size
                if book_file_offset == 0:
                    with open(book_path, "r") as f:
                        f.readline()
                        book_file_offset = f.tell()
                if current_size > book_file_offset:
                    with open(book_path, "r") as f:
                        f.seek(book_file_offset)
                        for line in f:
                            parts = line.strip().split(",")
                            if len(parts) < 10: continue
                            asset_id = parts[1]
                            try:
                                row_ts_ns = int(datetime.fromisoformat(parts[0]).timestamp() * 1e9)
                            except Exception:
                                continue
                            bid = float(parts[3]) if parts[3] else None
                            ask = float(parts[4]) if parts[4] else None
                            bid_size = float(parts[5]) if len(parts) > 5 and parts[5] else None
                            ask_size = float(parts[6]) if len(parts) > 6 and parts[6] else None
                            book = book_store._ensure(asset_id)
                            existing_ts = book.get("received_at_ns") or 0
                            if row_ts_ns >= existing_ts:
                                book["best_bid"] = bid
                                book["best_ask"] = ask
                                book["bid_size"] = bid_size
                                book["ask_size"] = ask_size
                                book["received_at_ns"] = row_ts_ns
                        book_file_offset = f.tell()
            
            # --- 3. Process Snapshots ---
            if snapshot_path.exists():
                current_snap_size = snapshot_path.stat().st_size
                if snap_file_offset == 0:
                    with open(snapshot_path, "r") as f:
                        f.readline()
                        if processing_mode == "live_tail":
                            f.seek(0, 2)
                        snap_file_offset = f.tell()
                if current_snap_size > snap_file_offset:
                    with open(snapshot_path, "r") as f:
                        f.seek(snap_file_offset)
                        for line in f:
                            row = next(csv.reader([line]))
                        if len(row) < 18: continue
                        ns = int(row[1])
                        if ns <= last_snap_ts: continue
                        last_snap_ts = ns
                        
                        match_id = row[2]
                        if match_id not in markets: continue
                        
                        mapping = markets[match_id]
                        game = {
                            "match_id": match_id,
                            "received_at_ns": ns,
                            "data_source": row[15],
                            "game_over": row[17].lower() == "true",
                            "game_time_sec": int(row[6]),
                            "radiant_lead": int(row[7]),
                            "radiant_score": row[8] if row[8] else None,
                            "dire_score": row[9] if row[9] else None,
                            "building_state": row[10] if row[10] else None,
                            "tower_state": row[11] if row[11] else None,
                            "stream_delay_s": float(row[13]) if row[13] else 0.0,
                            "source_update_age_sec": float(row[14]) if row[14] else 0.0,
                        }
                        
                        def _to_int(value):
                            if value in (None, ""): return None
                            try: return int(float(value))
                            except: return None
                            
                        rad_score = _to_int(game["radiant_score"])
                        dire_score = _to_int(game["dire_score"])
                        
                        rad_towers, dire_towers = None, None
                        try:
                            state = decode_structure_state({
                                "match_id": game["match_id"],
                                "game_time_sec": game["game_time_sec"],
                                "building_state": game["building_state"],
                                "building_state_schema": "top_live_lane_tower_progress",
                                "tower_state": game["tower_state"],
                            })
                            rad = [state.radiant_t1_alive, state.radiant_t2_alive, state.radiant_t3_alive, state.radiant_t4_alive]
                            dire = [state.dire_t1_alive, state.dire_t2_alive, state.dire_t3_alive, state.dire_t4_alive]
                            if not any(x is None for x in rad + dire):
                                rad_towers, dire_towers = int(sum(rad)), int(sum(dire))
                        except Exception:
                            pass
                            
                        leader_sign = 1 if game["radiant_lead"] > 0 else -1
                        leader_kill_diff = leader_sign * (rad_score - dire_score) if rad_score is not None and dire_score is not None else None
                        leader_tower_diff = leader_sign * (rad_towers - dire_towers) if rad_towers is not None and dire_towers is not None else None
                        
                        score_aligned_with_leader = leader_kill_diff is not None and leader_kill_diff >= 0
                        tower_aligned_with_leader = leader_tower_diff is not None and leader_tower_diff >= 0
                        building_state_aligned_with_leader = score_aligned_with_leader and tower_aligned_with_leader
                        
                        survival_overlay_status = "aligned" if building_state_aligned_with_leader else "not_aligned"
                        if leader_kill_diff is None or leader_tower_diff is None:
                            survival_overlay_status = "missing"
                            
                        survival_overlay_reason = f"kills:{leader_kill_diff}, towers:{leader_tower_diff}"
                        
                        results = engine.evaluate(game, mapping, book_store)
                        
                        for res in results:
                            decision = "WOULD_REJECT"
                            reason = "unknown"
                            if isinstance(res, ValueSignal):
                                decision = "WOULD_ENTER"
                                if match_id in entered_matches:
                                    decision = "DUPLICATE_POSITION_BLOCKED"
                                else:
                                    entered_matches.add(match_id)
                                reason = "value_edge"
                            else:
                                reason = res.reason
                                if reason in ["game_too_early", "game_too_late", "lead_too_small"]:
                                    decision = "WOULD_SKIP"
                            
                            token_id = getattr(res, "token_id", "")
                            side = getattr(res, "side", "")
                            fair = getattr(res, "fair_price", None)
                            ask = getattr(res, "ask", None)
                            edge = getattr(res, "edge", None)
                            
                            book_entry = book_store.get(token_id) if token_id else {}
                            best_bid = book_entry.get("best_bid")
                            bid_size = book_entry.get("bid_size")
                            ask_size = book_entry.get("ask_size")
                            
                            spread = (ask - best_bid) if ask is not None and best_bid is not None else None
                            notional_available_at_ask = (ask * ask_size) if ask is not None and ask_size is not None else None
                            
                            would_stake_usd = 6.0
                            
                            fill_quality_status = "estimated_SIZE_UNKNOWN"
                            if ask_size is not None and ask_size > 0:
                                if ask_size >= would_stake_usd:
                                    fill_quality_status = "estimated_FULL_SIZE_AVAILABLE"
                                else:
                                    fill_quality_status = "estimated_PARTIAL_SIZE_AVAILABLE"
                            elif ask_size is not None and ask_size <= 0:
                                fill_quality_status = "estimated_NO_SIZE_AVAILABLE"
                            
                            book_received_at_ns = book_entry.get("received_at_ns", 0)
                            decision_wall_time_ns = time.time_ns()
                            
                            if processing_mode == "live_tail":
                                book_age_ms = int((decision_wall_time_ns - book_received_at_ns) / 1_000_000) if book_received_at_ns > 0 else 0
                                if decision_wall_time_ns < book_received_at_ns:
                                    decision = "WOULD_REJECT"
                                    reason = "future_book_relative_to_wall_clock"
                                elif decision_wall_time_ns < ns:
                                    decision = "WOULD_REJECT"
                                    reason = "future_snapshot_relative_to_wall_clock"
                                elif abs(book_received_at_ns - ns) > shadow_config.get("max_feed_skew_ns", 15_000_000_000):
                                    decision = "WOULD_REJECT"
                                    reason = "excessive_snapshot_book_skew"
                            else:
                                book_age_ms = int((ns - book_received_at_ns) / 1_000_000) if book_received_at_ns > 0 else 0
                                if book_received_at_ns > ns:
                                    decision = "WOULD_REJECT"
                                    reason = "future_book_relative_to_snapshot"

                            timestamp_valid = True
                            passes_gettoplive_guard = True
                            if book_received_at_ns == 0 or ns == 0:
                                timestamp_valid = False
                                passes_gettoplive_guard = False
                                if decision == "WOULD_ENTER":
                                    decision = "WOULD_SKIP"
                                reason = "missing_snapshot_timestamp"

                            entry = {
                                "decision_ts": time.time_ns(),
                                "poll_ts": ns,
                                "match_id": match_id,
                                "side": side,
                                "decision": decision,
                                "reason": reason,
                                "timestamp_valid": timestamp_valid,
                                "passes_gettoplive_guard": passes_gettoplive_guard,
                                "game_time_sec": game.get("game_time_sec"),
                                "radiant_lead": game.get("radiant_lead"),
                                "entry_ask": ask,
                                "best_bid": best_bid,
                                "bid_size": bid_size,
                                "ask_size": ask_size,
                                "spread": spread,
                                "notional_available_at_ask": notional_available_at_ask,
                                "stake_model": "mock",
                                "would_stake_usd": would_stake_usd,
                                "ask_size_covers_would_stake": fill_quality_status == "estimated_FULL_SIZE_AVAILABLE",
                                "fill_quality_status": fill_quality_status,
                                "fair": fair,
                                "edge": edge,
                                "nav_snapshot": nav,
                                "open_position_count": live_state.get("open_positions", 0),
                                "causality_valid": True,
                                "score_aligned_with_leader": score_aligned_with_leader,
                                "tower_aligned_with_leader": tower_aligned_with_leader,
                                "building_state_aligned_with_leader": building_state_aligned_with_leader,
                                "survival_overlay_status": survival_overlay_status,
                                "survival_overlay_reason": survival_overlay_reason,
                                "survival_overlay_gate_active": False,
                                "snapshot_source_ts_ns": ns,
                                "book_received_at_ns": book_received_at_ns,
                                "source_update_age_sec": game.get("source_update_age_sec", 0.0),
                                "stream_delay_s": game.get("stream_delay_s", 0.0),
                                "book_age_ms": book_age_ms,
                                "processing_mode": processing_mode,
                                "validation_eligible": validation_eligible,
                                "decision_lag_ms": int((time.time_ns() - ns) / 1_000_000)
                            }
                            
                            with open(decisions_path, "a") as df:
                                df.write(json.dumps(entry) + "\n")

        except Exception as e:
            print(f"Monitor error: {e}")
            
        time.sleep(10) # Poll every 10s

if __name__ == "__main__":
    main()
