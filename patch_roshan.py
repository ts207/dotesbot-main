import csv
import json
from pathlib import Path

def patch_snapshots():
    jsonl_path = Path("logs/liveleague_raw.jsonl")
    csv_path = Path("logs/raw_snapshots.csv")
    out_path = Path("logs/raw_snapshots.csv.tmp")
    
    if not jsonl_path.exists():
        print("JSONL not found")
        return
        
    print("Building Roshan lookup (limited to 50k entries for speed)...")
    lookup = {}
    with open(jsonl_path, "r") as f:
        for line in f:
            try:
                data = json.loads(line)
                mid = data.get("match_id")
                gt = data.get("game_time_sec")
                rt = data.get("raw", {}).get("roshan_respawn_timer")
                if mid and gt is not None and rt is not None:
                    lookup[(str(mid), int(gt))] = int(rt)
            except:
                continue
    
    print(f"Lookup built: {len(lookup)} points")

    with open(csv_path, "r") as f_in, open(out_path, "w", newline="") as f_out:
        reader = csv.DictReader(f_in)
        headers = reader.fieldnames
        if "roshan_respawn_timer" not in headers:
            headers.insert(headers.index("stream_delay_s"), "roshan_respawn_timer")
        
        writer = csv.DictWriter(f_out, fieldnames=headers)
        writer.writeheader()
        
        for row in reader:
            mid = row.get("match_id")
            gt = row.get("game_time_sec")
            rt = 0
            if gt:
                rt = lookup.get((str(mid), int(float(gt))), 0)
            row["roshan_respawn_timer"] = rt
            writer.writerow(row)
            
    out_path.replace(csv_path)
    print("Done.")

if __name__ == "__main__":
    patch_snapshots()
