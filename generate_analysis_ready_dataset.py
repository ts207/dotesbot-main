import os
import json
import pandas as pd
from pathlib import Path
from collections import defaultdict

class OutcomeAggregator:
    def __init__(self, root_dir="."):
        self.root_dir = Path(root_dir)

    def get_confirmed_outcomes(self) -> dict[str, set[str]]:
        """
        Returns a mapping of match_id -> set of confirmed token_ids.
        If a match is confirmed but no tokens are specified, the set will be empty.
        """
        outcomes = defaultdict(set)

        # 1. strategy_outcomes.csv
        outcomes_path = self.root_dir / "logs" / "strategy_outcomes.csv"
        if outcomes_path.exists():
            try:
                df = pd.read_csv(outcomes_path)
                # Filter for settled/won outcomes
                settled_statuses = ["won", "resolved", "known", "settled", "win", "loss"]
                if "settlement_status" in df.columns:
                    df = df[df["settlement_status"].astype(str).str.lower().isin(settled_statuses)]
                
                if "match_id" in df.columns:
                    for _, row in df.iterrows():
                        m_id = str(row["match_id"])
                        if "token_id" in df.columns and pd.notna(row["token_id"]):
                            outcomes[m_id].add(str(row["token_id"]))
                        else:
                            if m_id not in outcomes:
                                outcomes[m_id] = set()
            except Exception as e:
                print(f"Error reading {outcomes_path}: {e}")

        # 2. shadow_outcomes_cache.json
        shadow_path = self.root_dir / "logs" / "shadow_outcomes_cache.json"
        if shadow_path.exists():
            try:
                with open(shadow_path, "r") as f:
                    data = json.load(f)
                    for k, v in data.items():
                        # Support composite keys if they exist "match_id:token_id"
                        if ":" in str(k):
                            m_id, t_id = str(k).split(":", 1)
                            outcomes[m_id].add(t_id)
                        else:
                            m_id = str(k)
                            if m_id not in outcomes:
                                outcomes[m_id] = set()
                            # Support token info in value if available
                            if isinstance(v, dict):
                                if "token_id" in v:
                                    outcomes[m_id].add(str(v["token_id"]))
                                elif "tokens" in v and isinstance(v["tokens"], list):
                                    for t in v["tokens"]:
                                        outcomes[m_id].add(str(t))
            except Exception as e:
                print(f"Error reading {shadow_path}: {e}")

        # 3. settlement_shadow.csv (the shadow ledger)
        shadow_ledger = self.root_dir / "logs" / "settlement_shadow.csv"
        if shadow_ledger.exists():
            try:
                df = pd.read_csv(shadow_ledger)
                if "status" in df.columns:
                    valid_df = df[df["status"].str.upper().isin(["WIN", "LOSS"])]
                    for _, row in valid_df.iterrows():
                        m_id = str(row["match_id"])
                        if "token_id" in df.columns and pd.notna(row["token_id"]):
                            outcomes[m_id].add(str(row["token_id"]))
                        else:
                            if m_id not in outcomes:
                                outcomes[m_id] = set()
            except Exception as e:
                print(f"Error reading {shadow_ledger}: {e}")

        # 4. GAME_ENDED events in data_v2/dota_events
        event_dir = self.root_dir / "data_v2" / "dota_events"
        if event_dir.exists():
            for pfile in event_dir.glob("**/*.parquet"):
                try:
                    df = pd.read_parquet(pfile, columns=["event_type", "match_id"])
                    winners = df[df["event_type"] == "GAME_ENDED"]
                    for m_id in winners["match_id"].unique():
                        m_id_str = str(m_id)
                        if m_id_str not in outcomes:
                            outcomes[m_id_str] = set()
                except Exception as e:
                    print(f"Error reading {pfile}: {e}")

        return dict(outcomes)

if __name__ == "__main__":
    agg = OutcomeAggregator()
    outcomes = agg.get_confirmed_outcomes()
    match_count = len(outcomes)
    token_count = sum(len(ts) for ts in outcomes.values())
    print(f"Found {match_count} confirmed matches and {token_count} confirmed tokens.")
