import csv
import time
import os
import json
from datetime import datetime, timezone

def ns_to_iso(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc).isoformat(timespec="milliseconds")

def replay():
    rich_path = "logs/rich_context.csv"
    raw_path = "logs/raw_snapshots.csv"
    
    # Active logs for dashboard
    active_rich = "logs/rich_context.csv.active" # We use a suffix to not overwrite actual bot logs if it starts
    active_raw = "logs/raw_snapshots.csv.active"
    
    # Actually dashboard.py uses RICH_CONTEXT_CSV_PATH from config
    # I'll just temporarily redirect dashboard.py to read from .replay files
    
    # Read first 100 rows of rich context to simulate a game
    rows = []
    with open(rich_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i > 100: break
            rows.append(row)
            
    print(f"Replaying {len(rows)} rows...")
    
    # Prepare active files
    headers = list(rows[0].keys())
    with open("logs/rich_context.csv.replay", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        
    raw_headers = ["received_at_utc", "received_at_ns", "match_id", "lobby_id", "league_id", "game_time_sec", "radiant_lead", "radiant_score", "dire_score", "building_state", "tower_state", "roshan_respawn_timer", "stream_delay_s", "source_update_age_sec", "data_source", "spectators", "game_over"]
    with open("logs/raw_snapshots.csv.replay", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=raw_headers)
        writer.writeheader()

    for row in rows:
        now_ns = time.time_ns()
        now_iso = ns_to_iso(now_ns)
        
        # Update row with fresh timestamps
        row["timestamp_utc"] = now_iso
        row["received_at_ns"] = str(now_ns)
        
        with open("logs/rich_context.csv.replay", "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writerow(row)
            
        # Write matching raw snapshot
        raw_row = {
            "received_at_utc": now_iso,
            "received_at_ns": now_ns,
            "match_id": row["match_id"],
            "lobby_id": row["lobby_id"],
            "league_id": row["league_id"],
            "game_time_sec": row["game_time_sec"],
            "radiant_lead": row["net_worth_diff"],
            "radiant_score": row["radiant_score"],
            "dire_score": row["dire_score"],
            "building_state": "0", # Mock
            "tower_state": row["radiant_tower_state"],
            "roshan_respawn_timer": "0",
            "stream_delay_s": "30",
            "source_update_age_sec": "0",
            "data_source": "live_league",
            "spectators": "100",
            "game_over": "False"
        }
        with open("logs/raw_snapshots.csv.replay", "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=raw_headers)
            writer.writerow(raw_row)
            
        print(f"Pushed game time {row['game_time_sec']}s for match {row['match_id']}")
        time.sleep(2) # Push every 2 seconds

if __name__ == "__main__":
    replay()
