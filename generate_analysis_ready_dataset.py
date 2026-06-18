import os
import json
import pandas as pd
from pathlib import Path

class OutcomeAggregator:
    def __init__(self, root_dir="."):
        self.root_dir = Path(root_dir)

    def get_confirmed_outcomes(self):
        confirmed_matches = set()
        confirmed_tokens = set()

        # 1. strategy_outcomes.csv
        outcomes_path = self.root_dir / "logs" / "strategy_outcomes.csv"
        if outcomes_path.exists():
            try:
                df = pd.read_csv(outcomes_path)
                if "match_id" in df.columns:
                    confirmed_matches.update(df["match_id"].astype(str).unique())
                if "token_id" in df.columns:
                    confirmed_tokens.update(df["token_id"].astype(str).unique())
            except Exception as e:
                print(f"Error reading {outcomes_path}: {e}")

        # 2. shadow_outcomes_cache.json
        shadow_path = self.root_dir / "logs" / "shadow_outcomes_cache.json"
        if shadow_path.exists():
            try:
                with open(shadow_path, "r") as f:
                    data = json.load(f)
                    confirmed_matches.update(str(k) for k in data.keys())
            except Exception as e:
                print(f"Error reading {shadow_path}: {e}")

        # 3. GAME_ENDED events in data_v2/dota_events
        event_dir = self.root_dir / "data_v2" / "dota_events"
        if event_dir.exists():
            for pfile in event_dir.glob("**/*.parquet"):
                try:
                    df = pd.read_parquet(pfile, columns=["event_type", "match_id"])
                    winners = df[df["event_type"] == "GAME_ENDED"]
                    confirmed_matches.update(winners["match_id"].astype(str).unique())
                except Exception as e:
                    print(f"Error reading {pfile}: {e}")

        return confirmed_matches, confirmed_tokens

if __name__ == "__main__":
    agg = OutcomeAggregator()
    matches, tokens = agg.get_confirmed_outcomes()
    print(f"Found {len(matches)} confirmed matches and {len(tokens)} confirmed tokens.")
