import csv
import os
import subprocess
from pathlib import Path
from io import StringIO

def _read_csv(path, tail_lines=None):
    p = Path(path)
    if not p.exists():
        return []
    try:
        if tail_lines and p.stat().st_size > 1000: # smaller for test
            cmd = ["tail", "-n", str(tail_lines), str(p)]
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
            if proc.returncode == 0:
                with p.open(encoding="utf-8") as f:
                    header = f.readline()
                return list(csv.DictReader(StringIO(header + proc.stdout)))
        
        with p.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        print(f"Error: {e}")
        return []

# Test with signals.csv
data = _read_csv("logs/signals.csv", tail_lines=10)
print(f"Read {len(data)} rows from signals.csv")
if data:
    print(f"First row keys: {list(data[0].keys())}")
    print(f"First row: {data[0]}")
