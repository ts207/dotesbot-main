import pandas as pd
from pathlib import Path
import os

target_columns = ["timestamp_ns", "match_id", "market_id", "yes_token_id", "no_token_id"]

for root, dirs, files in os.walk("/home/tstuv"):
    for f in files:
        if f.endswith(".csv"):
            try:
                df = pd.read_csv(os.path.join(root, f), nrows=0)
                if all(c in df.columns for c in target_columns):
                    print(f"MATCH: {os.path.join(root, f)}")
            except Exception:
                pass
