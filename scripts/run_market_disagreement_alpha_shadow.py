#!/usr/bin/env python3
"""
Market Disagreement Alpha Shadow Monitor.
Continually tails live logs to evaluate 'deep_discount_high_edge_leader_candidate' and 'market_lag_candidate'.
"""
import json
import os
import sys
import time
import math
import argparse
from pathlib import Path
import csv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from value_engine import ValueEngine, ValueSignal, ValueReject
from poly_ws import BookStore
from structure_state import decode_structure_state
from backtest_value_engine import signal_side

CONFIG_PATH = REPO_ROOT / "configs" / "market_disagreement_alpha_v1.json"
with open(CONFIG_PATH, "r") as f:
    ALPHA_CONFIG = json.load(f)

# Ensure value engine is enabled for baseline calculation
os.environ["VALUE_ENGINE_ENABLED"] = "true"
os.environ["ENABLE_VALUE_TRADING"] = "false"

def load_markets_mapping():
    import yaml
    try:
        with open(REPO_ROOT / "markets.yaml", "r") as f:
            data = yaml.safe_load(f)
            return {str(m["dota_match_id"]): m for m in data.get("markets", []) if m.get("dota_match_id")}
    except Exception:
        return {}

def evaluate_alpha_rules(game, mapping, book_store, v1_results, history, processing_mode='backlog_replay', validation_eligible=False, decision_wall_time_ns=None):
    # Base requirements
    match_id = game["match_id"]
    game_time = game.get("game_time_sec")
    lead = game.get("radiant_lead")
    cur_ns = game.get("received_at_ns")
    
    decisions = []
    
    if game.get("data_source") != "top_live" or game.get("game_over") or game_time is None or lead is None:
        return decisions
        
    lead = int(lead)
    side, direction = signal_side(mapping, lead)
    token = mapping.get("yes_token_id") if side == "YES" else mapping.get("no_token_id") if side == "NO" else None
    
    book_entry = book_store.get(token) if token else None
    if not book_entry:
        return decisions
    book_ns = int(book_entry.get("received_at_ns", 0))
    decision_wall_time_ns = int(time.time() * 1e9)
    
    snapshot_age_sec = (decision_wall_time_ns - cur_ns) / 1e9
    book_age_sec = (decision_wall_time_ns - book_ns) / 1e9
    book_age_ms = int(book_age_sec * 1000)
    snapshot_book_skew_sec = (book_ns - cur_ns) / 1e9
    
    causality_valid = (decision_wall_time_ns >= cur_ns) and (decision_wall_time_ns >= book_ns)
    
    if abs(snapshot_book_skew_sec) > 15.0:
        feed_skew_status = "EXCESSIVE_SKEW"
    elif snapshot_book_skew_sec > 0:
        feed_skew_status = "BOOK_NEWER_THAN_SNAPSHOT"
    elif snapshot_book_skew_sec < 0:
        feed_skew_status = "SNAPSHOT_NEWER_THAN_BOOK"
    else:
        feed_skew_status = "OK"

    ask = book_entry.get("best_ask")
    bid = book_entry.get("best_bid")
    bid_size = book_entry.get("bid_size")
    ask_size = book_entry.get("ask_size")
    
    spread = None
    notional_available_at_ask = None
    
    would_stake_usd = 6.0
    fill_quality_status = "estimated_SIZE_UNKNOWN"

    if ask is not None and bid is not None:
        try:
            ask = float(ask)
            bid = float(bid)
            spread = ask - bid
        except:
            pass
            
    if ask is not None and ask_size is not None:
        try:
            ask_size = float(ask_size)
            notional_available_at_ask = ask * ask_size
            if ask_size > 0:
                if ask_size >= would_stake_usd:
                    fill_quality_status = "estimated_FULL_SIZE_AVAILABLE"
                else:
                    fill_quality_status = "estimated_PARTIAL_SIZE_AVAILABLE"
            elif ask_size <= 0:
                fill_quality_status = "estimated_NO_SIZE_AVAILABLE"
        except:
            pass
            
    # Need fair and edge. Try to get it from v1_results if available.
    fair = None
    edge = None
    v1_would_enter = False
    for res in v1_results:
        if getattr(res, "side", "") == side and getattr(res, "token_id", "") == token:
            fair = getattr(res, "fair_price", fair)
            edge = getattr(res, "edge", edge)
            if isinstance(res, ValueSignal):
                v1_would_enter = True
                
    # If fair/edge not computed by v1_results (due to early reject), we must compute it.
    if fair is None and ask is not None:
        import winprob
        # lead slope
        target = cur_ns - 300_000_000_000
        past = None
        for hist_ns, hist_lead in history.get(match_id, []):
            if hist_ns <= target:
                past = hist_lead
            else:
                break
        slope_rad = 0.0 if past is None else float(lead - past)
        
        rtid, dtid = game.get("radiant_team_id"), game.get("dire_team_id")
        rname, dname = game.get("radiant_team"), game.get("dire_team")
        
        if direction == "radiant":
            elo_diff = winprob.elo_diff(rtid, dtid, rname, dname)
            lead_slope = slope_rad
        else:
            elo_diff = winprob.elo_diff(dtid, rtid, dname, rname)
            lead_slope = -slope_rad
            
        fair = winprob.fair(abs(lead), game_time, elo_diff, lead_slope, None)
        edge = fair - ask
        
    # Evaluate deep_discount_high_edge_leader_candidate
    dd_cfg = ALPHA_CONFIG["deep_discount_high_edge_leader_candidate"]
    dd_would_enter = False
    dd_reason = "unknown"
    timestamp_valid = True
    passes_gettoplive_guard = True
    
    if book_ns == 0 or cur_ns == 0:
        timestamp_valid = False
        passes_gettoplive_guard = False
        dd_reason = "missing_snapshot_timestamp"
    elif ask is None or math.isnan(ask):
        dd_reason = "missing_ask"
    elif feed_skew_status != "OK":
        dd_reason = "excessive_skew"
    elif book_age_ms < 0 or book_age_ms > dd_cfg["max_book_age_ms"]:
        dd_reason = "book_stale"
    elif game_time < dd_cfg["min_game_time_sec"] or game_time > dd_cfg["max_game_time_sec"]:
        dd_reason = "game_time_bounds"
    elif ask > dd_cfg["max_ask"]:
        dd_reason = "ask_too_high"
    elif edge is None or edge < dd_cfg["min_edge"]:
        dd_reason = "edge_too_small"
    else:
        dd_would_enter = True
        dd_reason = "alpha_edge"
        
    decisions.append({
        "alpha_rule_id": "deep_discount_high_edge_leader_candidate",
        "alpha_rule_version": "v1",
        "alpha_diagnostic_only": dd_cfg["diagnostic_only"],
        "alpha_would_enter": dd_would_enter,
        "alpha_reject_reason": dd_reason,
        "overlaps_value_v1": v1_would_enter,
        "incremental_vs_value_v1": dd_would_enter and not v1_would_enter,
        "entry_ask": ask,
        "best_bid": bid,
        "bid_size": bid_size,
        "ask_size": ask_size,
        "spread": spread,
        "notional_available_at_ask": notional_available_at_ask,
        "stake_model": "mock",
        "would_stake_usd": would_stake_usd,
        "ask_size_covers_would_stake": fill_quality_status == "estimated_FULL_SIZE_AVAILABLE",
        "fill_quality_status": fill_quality_status,
        "book_age_ms": book_age_ms,
        "book_received_at_ns": book_ns,
        "fair": fair,
        "edge": edge,
        "game_time_sec": game_time,
        "networth_lead": lead,
        "causality_valid": causality_valid,
        "snapshot_age_sec": snapshot_age_sec,
        "book_age_sec": book_age_sec,
        "snapshot_book_skew_sec": snapshot_book_skew_sec,
        "feed_skew_status": feed_skew_status,
        "timestamp_valid": timestamp_valid,
        "passes_gettoplive_guard": passes_gettoplive_guard,
        "processing_mode": processing_mode,
        "validation_eligible": validation_eligible
    })
    
    # Evaluate market_lag_candidate
    ml_cfg = ALPHA_CONFIG["market_lag_candidate"]
    ml_would_enter = False
    ml_reason = "unknown"
    
    if book_ns == 0 or cur_ns == 0:
        ml_reason = "missing_snapshot_timestamp"
    elif ask is None or math.isnan(ask):
        ml_reason = "missing_ask"
    elif feed_skew_status != "OK":
        ml_reason = "excessive_skew"
    elif book_age_ms < 0 or book_age_ms > ml_cfg["max_book_age_ms"]:
        ml_reason = "book_stale"
    elif game_time < ml_cfg["min_game_time_sec"] or game_time > ml_cfg["max_game_time_sec"]:
        ml_reason = "game_time_bounds"
    elif fair is None or fair < ml_cfg["min_fair"]:
        ml_reason = "fair_too_low"
    elif ask < ml_cfg["min_ask"] or ask > ml_cfg["max_ask"]:
        ml_reason = "ask_out_of_bounds"
    elif edge is None or edge < ml_cfg["min_edge"] or edge > ml_cfg["max_edge"]:
        ml_reason = "edge_out_of_bounds"
    else:
        ml_would_enter = True
        ml_reason = "alpha_edge"
        
    decisions.append({
        "alpha_rule_id": "market_lag_candidate",
        "alpha_rule_version": "v1",
        "alpha_diagnostic_only": ml_cfg["diagnostic_only"],
        "alpha_would_enter": ml_would_enter,
        "alpha_reject_reason": ml_reason,
        "overlaps_value_v1": v1_would_enter,
        "incremental_vs_value_v1": ml_would_enter and not v1_would_enter,
        "entry_ask": ask,
        "best_bid": bid,
        "bid_size": bid_size,
        "ask_size": ask_size,
        "spread": spread,
        "notional_available_at_ask": notional_available_at_ask,
        "stake_model": "mock",
        "would_stake_usd": would_stake_usd,
        "ask_size_covers_would_stake": fill_quality_status == "estimated_FULL_SIZE_AVAILABLE",
        "fill_quality_status": fill_quality_status,
        "book_age_ms": book_age_ms,
        "book_received_at_ns": book_ns,
        "fair": fair,
        "edge": edge,
        "game_time_sec": game_time,
        "networth_lead": lead,
        "causality_valid": causality_valid,
        "snapshot_age_sec": snapshot_age_sec,
        "book_age_sec": book_age_sec,
        "snapshot_book_skew_sec": snapshot_book_skew_sec,
        "feed_skew_status": feed_skew_status,
        "timestamp_valid": timestamp_valid,
        "passes_gettoplive_guard": passes_gettoplive_guard,
        "processing_mode": processing_mode,
        "validation_eligible": validation_eligible
    })
        
    return decisions

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
    print("Starting Market Disagreement Alpha Shadow Monitor...")
    markets = load_markets_mapping()
    engine = ValueEngine()
    book_store = BookStore()
    
    decisions_path = REPO_ROOT / "logs" / "market_disagreement_alpha_shadow_decisions.jsonl"
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    
    snapshot_path = REPO_ROOT / "logs" / "raw_snapshots.csv"
    book_path = REPO_ROOT / "logs" / "book_events.csv"
    
    last_snap_ts = 0
    history = {} # match_id -> list of (ns, lead)
    
    snap_file_offset = 0
    while True:
        try:
            if book_path.exists():
                with open(book_path, "r") as f:
                    f.readline()
                    for line in f:
                        parts = line.strip().split(",")
                        if len(parts) < 10: continue
                        asset_id = parts[1]
                        bid = float(parts[3]) if parts[3] else None
                        ask = float(parts[4]) if parts[4] else None
                        bid_size = float(parts[5]) if len(parts) > 5 and parts[5] else None
                        ask_size = float(parts[6]) if len(parts) > 6 and parts[6] else None
                        book = book_store._ensure(asset_id)
                        book["best_bid"] = bid
                        book["best_ask"] = ask
                        book["bid_size"] = bid_size
                        book["ask_size"] = ask_size
                        from datetime import datetime
                        book["received_at_ns"] = int(datetime.fromisoformat(parts[0]).timestamp() * 1e9)
                        
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
                        
                        game = {
                            "match_id": match_id,
                            "received_at_ns": ns,
                            "data_source": row[15],
                            "game_over": row[17].lower() == "true",
                            "game_time_sec": int(row[6]) if row[6] else None,
                            "radiant_lead": int(row[7]) if row[7] else None,
                            "radiant_score": row[8] if row[8] else None,
                            "dire_score": row[9] if row[9] else None,
                            "building_state": row[10] if row[10] else None,
                            "tower_state": row[11] if row[11] else None,
                            "radiant_team_id": row[22] if len(row)>22 else None,
                            "dire_team_id": row[23] if len(row)>23 else None,
                            "radiant_team": row[20] if len(row)>20 else None,
                            "dire_team": row[21] if len(row)>21 else None,
                            "stream_delay_s": float(row[13]) if len(row)>13 and row[13] else 0.0,
                            "source_update_age_sec": float(row[14]) if len(row)>14 and row[14] else 0.0,
                        }
                        
                        if game["game_time_sec"] is not None and game["radiant_lead"] is not None:
                            if match_id not in history:
                                history[match_id] = []
                            history[match_id].append((ns, game["radiant_lead"]))
                            # Clean history
                            while history[match_id] and history[match_id][0][0] < ns - 400_000_000_000:
                                history[match_id].pop(0)
                        
                        v1_results = engine.evaluate(game, markets[match_id], book_store)
                        decision_wall_time_ns = time.time_ns()
                        
                        # We need structural alignment logic
                        def _to_int(val):
                            try: return int(float(val))
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
                            
                        leader_sign = 1 if (game["radiant_lead"] or 0) > 0 else -1
                        leader_kill_diff = leader_sign * (rad_score - dire_score) if rad_score is not None and dire_score is not None else None
                        leader_tower_diff = leader_sign * (rad_towers - dire_towers) if rad_towers is not None and dire_towers is not None else None
                        
                        score_aligned_with_leader = leader_kill_diff is not None and leader_kill_diff >= 0
                        tower_aligned_with_leader = leader_tower_diff is not None and leader_tower_diff >= 0
                        building_state_aligned_with_leader = score_aligned_with_leader and tower_aligned_with_leader
                        
                        # networth deltas
                        networth_delta_30s = None
                        networth_delta_60s = None
                        leader_changed_60s = False
                        lead_now = game["radiant_lead"]
                        if lead_now is not None:
                            target_30 = ns - 30_000_000_000
                            target_60 = ns - 60_000_000_000
                            past_30 = None
                            past_60 = None
                            for h_ns, h_lead in history.get(match_id, []):
                                if h_ns <= target_30: past_30 = h_lead
                                if h_ns <= target_60: past_60 = h_lead
                            if past_30 is not None:
                                networth_delta_30s = leader_sign * (lead_now - past_30)
                            if past_60 is not None:
                                networth_delta_60s = leader_sign * (lead_now - past_60)
                                if (lead_now > 0 and past_60 < 0) or (lead_now < 0 and past_60 > 0):
                                    leader_changed_60s = True
                        
                        alpha_decisions = evaluate_alpha_rules(game, markets[match_id], book_store, v1_results, history, processing_mode, validation_eligible, decision_wall_time_ns)
                        
                        with open(decisions_path, "a") as df:
                            for ad in alpha_decisions:
                                ad["decision_ts"] = time.time_ns()
                                ad["poll_ts"] = ns
                                ad["match_id"] = match_id
                                ad["score_aligned_with_leader"] = score_aligned_with_leader
                                ad["tower_aligned_with_leader"] = tower_aligned_with_leader
                                ad["building_state_aligned_with_leader"] = building_state_aligned_with_leader
                                ad["networth_delta_30s"] = networth_delta_30s
                                ad["networth_delta_60s"] = networth_delta_60s
                                ad["leader_changed_60s"] = leader_changed_60s
                                ad["snapshot_source_ts_ns"] = ns
                                ad["source_update_age_sec"] = game.get("source_update_age_sec", 0.0)
                                ad["stream_delay_s"] = game.get("stream_delay_s", 0.0)
                                ad["decision_lag_ms"] = int((time.time_ns() - ns) / 1_000_000)
                                
                                df.write(json.dumps(ad) + "\n")
                                
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Shadow Monitor error: {e}")
            
        time.sleep(10)

if __name__ == "__main__":
    main()
